from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from datetime import date
from werkzeug.security import generate_password_hash, check_password_hash
import os

TAX_RATE = 0.10  # 10% tax

# Pricing constants
WINTER_SURGE = 0.40  # Dec, Jan
SUMMER_SURGE = 0.30  # Jun–Aug
SPRING_SURGE = 0.20  # Mar–May

app = Flask(__name__)
app.secret_key = os.environ.get("HOTEL_SECRET", os.urandom(24))


# DB config
basedir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "hotel.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# MODELS
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), default="guest")  # guest or staff

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_type = db.Column(db.String(50))
    base_price = db.Column(db.Float)
    is_available = db.Column(db.Boolean, default=True)
    description = db.Column(db.String(300))


class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    room_id = db.Column(db.Integer, nullable=False)
    check_in = db.Column(db.String(20))
    check_out = db.Column(db.String(20))
    daily_price = db.Column(db.Float)  # ✅ NEW
    days = db.Column(db.Integer)       # ✅ NEW
    base_price = db.Column(db.Float)   # subtotal
    tax_amount = db.Column(db.Float)
    total_price = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(20), default="confirmed")
    paid = db.Column(db.Boolean, default=False)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Float)
    method = db.Column(db.String(20))
    status = db.Column(db.String(20), default="paid")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# HELPERS
def calculate_dynamic_price(base_price):
    """ Lightweight price function used for search & display only (no tax, no breakdown) """
    pricing = calculate_price_breakdown(base_price)
    return pricing["price_before_tax"]


def calculate_price_breakdown(base_price):
    month = datetime.now().month

    # ---------- Seasonal factor ----------
    if month in (12, 1):
        seasonal_factor = WINTER_SURGE
    elif month in (6, 7, 8):
        seasonal_factor = SUMMER_SURGE
    elif month in (3, 4, 5):
        seasonal_factor = SPRING_SURGE
    else:
        seasonal_factor = 0.00

    seasonal_price = round(base_price * (1 + seasonal_factor), 2)

    # ---------- Tax ----------
    tax_amount = round(seasonal_price * TAX_RATE, 2)
    total_price = round(seasonal_price + tax_amount, 2)

    return {
        "base_price": base_price,
        "seasonal_percent": int(seasonal_factor * 100),
        "price_before_tax": seasonal_price,
        "tax_amount": tax_amount,
        "total_price": total_price
    }


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)



# ROUTES (UI)
@app.route("/")
def home():
    user = current_user()
    return render_template("home.html", user=user)


# Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return redirect(url_for("register"))

        u = User(name=name, email=email, role="guest")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()

        flash("Registration successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            session["user_role"] = user.role
            flash("Logged in successfully", "success")

            if user.role == "staff":
                return redirect(url_for("admin_dashboard"))
            else:
                return redirect(url_for("home"))

        flash("Invalid credentials", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("home"))


# Admin: add room (staff only)
@app.route("/admin", methods=["GET", "POST"])
def admin_dashboard():
    user = current_user()
    if not user or user.role != "staff":
        flash("Admin access required", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        room_type = request.form["room_type"]
        base_price = float(request.form["base_price"])
        desc = request.form.get("description", "")

        r = Room(
            room_type=room_type,
            base_price=base_price,
            description=desc,
            is_available=True
        )
        db.session.add(r)
        db.session.commit()

        flash("Room added", "success")
        return redirect(url_for("admin_dashboard"))

    rooms = Room.query.all()
    reservations = Reservation.query.order_by(Reservation.created_at.desc()).all()
    return render_template(
        "admin_dashboard.html",
        rooms=rooms,
        reservations=reservations,
        user=user
    )

# Edit Room (Admin only)
@app.route("/edit_room/<int:room_id>", methods=["GET", "POST"])
def edit_room(room_id):
    user = current_user()
    if not user or user.role != "staff":
        flash("Admin access required", "danger")
        return redirect(url_for("login"))

    room = Room.query.get_or_404(room_id)

    if request.method == "POST":
        room.room_type = request.form["room_type"]
        room.base_price = float(request.form["base_price"])
        room.description = request.form.get("description", "")
        room.is_available = "is_available" in request.form

        db.session.commit()
        flash("Room updated successfully", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("edit_room.html", room=room, user=user)

# Delete Room (Admin only)
@app.route("/delete_room/<int:room_id>", methods=["POST"])
def delete_room(room_id):
    user = current_user()
    if not user or user.role != "staff":
        flash("Admin access required", "danger")
        return redirect(url_for("login"))

    room = Room.query.get_or_404(room_id)

    # Optional safety: prevent deleting booked rooms
    active_reservation = Reservation.query.filter_by(
        room_id=room.id,
        status="confirmed"
    ).first()

    if active_reservation:
        flash("Cannot delete a room with active reservations", "warning")
        return redirect(url_for("admin_dashboard"))

    db.session.delete(room)
    db.session.commit()

    flash("Room deleted successfully", "success")
    return redirect(url_for("admin_dashboard"))


# Search Rooms (UI)
@app.route("/search", methods=["GET", "POST"])
def search():
    user = current_user()

    # Admins are not allowed to search
    if user and user.role == "staff":
        flash("Admins cannot search rooms", "warning")
        return redirect(url_for("admin_dashboard"))

    rooms = Room.query.filter_by(is_available=True).all()
    filtered_rooms = []

    if request.method == "POST":
        room_type = request.form.get("room_type")
        max_price = request.form.get("max_price")

        for r in rooms:
            price_today = calculate_dynamic_price(r.base_price)
            r.price_today = price_today

            if room_type and room_type != "Any" and r.room_type != room_type:
                continue

            if max_price:
                try:
                    if price_today > float(max_price):
                        continue
                except:
                    pass

            filtered_rooms.append(r)
    else:
        for r in rooms:
            r.price_today = calculate_dynamic_price(r.base_price)
            filtered_rooms.append(r)

    return render_template("search.html", rooms=filtered_rooms, user=user)


# Book
@app.route("/book/<int:room_id>", methods=["GET", "POST"])
def book(room_id):
    user = current_user()
    if not user:
        flash("Login required to book", "warning")
        return redirect(url_for("login"))

    if user.role == "staff":
        flash("Admins cannot book rooms", "danger")
        return redirect(url_for("admin_dashboard"))

    room = Room.query.get_or_404(room_id)

    if not room.is_available:
        flash("Room is no longer available", "danger")
        return redirect(url_for("search"))

    price = calculate_dynamic_price(room.base_price)

    if request.method == "POST":
        check_in = request.form["check_in"]
        check_out = request.form["check_out"]
        payment_method = request.form["payment_method"]

        check_in_date = datetime.strptime(check_in, "%Y-%m-%d")
        check_out_date = datetime.strptime(check_out, "%Y-%m-%d")
        days = (check_out_date - check_in_date).days
        if days <= 0:
            days = 1

        pricing = calculate_price_breakdown(room.base_price)
        daily_price = pricing["price_before_tax"]
        subtotal = round(daily_price * days, 2)
        tax_amount = round(subtotal * TAX_RATE, 2)
        total_price = round(subtotal + tax_amount, 2)

        res = Reservation(
            user_id=user.id,
            room_id=room.id,
            check_in=check_in,
            check_out=check_out,
            daily_price=daily_price,
            days=days,
            base_price=subtotal,
            tax_amount=tax_amount,
            total_price=total_price,
            status="confirmed",
            paid=(payment_method != "")
        )

        room.is_available = False
        db.session.add(res)
        db.session.commit()

        flash("Room booked successfully", "success")
        return redirect(url_for("invoice", reservation_id=res.id))

    return render_template(
        "book.html",
        room=room,
        price=price,
        user=user,
        date=date
    )


# Invoice display
@app.route("/invoice/<int:reservation_id>")
def invoice(reservation_id):
    user = current_user()
    res = Reservation.query.get_or_404(reservation_id)
    room = Room.query.get(res.room_id)
    pay = Payment.query.filter_by(reservation_id=res.id).first()
    return render_template(
        "invoice.html",
        reservation=res,
        room=room,
        payment=pay,
        user=user
    )


# Payment page
@app.route("/pay/<int:reservation_id>", methods=["GET", "POST"])
def pay_page(reservation_id):
    user = current_user()
    resv = Reservation.query.get_or_404(reservation_id)

    if request.method == "POST":
        method = request.form["method"]
        amount = float(request.form["amount"])

        p = Payment(
            reservation_id=resv.id,
            amount=amount,
            method=method,
            status="paid"
        )
        resv.paid = True
        db.session.add(p)
        db.session.commit()

        flash("Payment successful", "success")
        return redirect(url_for("invoice", reservation_id=resv.id))

    return render_template("pay.html", reservation=resv, user=user)


# API-like endpoints
@app.route("/api/search_rooms", methods=["GET"])
def api_search_rooms():
    rooms = Room.query.filter_by(is_available=True).all()
    output = []

    for r in rooms:
        output.append({
            "room_id": r.id,
            "room_type": r.room_type,
            "price_today": calculate_dynamic_price(r.base_price),
            "description": r.description
        })

    return jsonify(output)


# initialize db
def init_db():
    with app.app_context():
        db.create_all()

        admins = [
            {
                "name": "Admin One",
                "email": "admin1@hotel.local",
                "password": "adminpass1"
            },
            {
                "name": "Admin Two",
                "email": "admin2@hotel.local",
                "password": "adminpass2"
            }
        ]

        for a in admins:
            if not User.query.filter_by(email=a["email"]).first():
                admin = User(
                    name=a["name"],
                    email=a["email"],
                    role="staff"
                )
                admin.set_password(a["password"])
                db.session.add(admin)

        db.session.commit()


if __name__ == "__main__":
    init_db()
    app.run()


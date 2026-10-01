import os
import sqlite3
import math
import csv
import io
import re
import uuid
from datetime import date, timedelta
from flask import Flask, Response, jsonify, render_template, request, redirect, url_for, session, flash, g, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from nutrition_api import NutritionServiceError, get_food_by_code, search_foods

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "nutritrack-secret-2024")

DATABASE = os.path.join(os.path.dirname(__file__), "nutritrack.db")

SAMPLE_FOODS = [
    ("Chicken Breast", 165, 31, 0, 3.6, "Protein", "Lean grilled chicken breast, excellent protein source"),
    ("Brown Rice", 216, 5, 45, 1.8, "Grains", "Cooked brown rice, complex carbohydrates for sustained energy"),
    ("Avocado", 160, 2, 9, 15, "Fats", "Fresh avocado, rich in healthy monounsaturated fats"),
    ("Whole Egg", 78, 6, 0.6, 5, "Protein", "Large whole egg, complete protein with essential nutrients"),
    ("Greek Yogurt", 59, 10, 3.6, 0.4, "Dairy", "Plain non-fat Greek yogurt, probiotic-rich dairy"),
    ("Salmon", 208, 20, 0, 13, "Protein", "Atlantic salmon fillet, omega-3 fatty acids powerhouse"),
    ("Sweet Potato", 86, 1.6, 20, 0.1, "Vegetables", "Baked sweet potato, rich in beta-carotene and fiber"),
    ("Oats", 389, 17, 66, 7, "Grains", "Rolled oats, high fiber whole grain for breakfast"),
    ("Banana", 89, 1.1, 23, 0.3, "Fruits", "Fresh banana, natural sugars and potassium-rich"),
    ("Almonds", 579, 21, 22, 50, "Nuts", "Raw almonds, nutrient-dense snack with healthy fats"),
    ("Broccoli", 34, 2.8, 7, 0.4, "Vegetables", "Fresh broccoli, antioxidant-packed cruciferous vegetable"),
    ("Quinoa", 120, 4.4, 21, 1.9, "Grains", "Cooked quinoa, complete plant-based protein and grain"),
    ("Lentils", 116, 9, 20, 0.4, "Legumes", "Cooked green lentils, iron-rich plant protein"),
    ("Cottage Cheese", 98, 11, 3.4, 4.3, "Dairy", "Low-fat cottage cheese, versatile high-protein dairy"),
    ("Spinach", 23, 2.9, 3.6, 0.4, "Vegetables", "Fresh spinach leaves, iron and vitamin K powerhouse"),
]

SAMPLE_RECIPES = [
    (
        "Chicken, Rice & Broccoli Bowl",
        "A protein-forward bowl with whole grains and vegetables.",
        "Lunch",
        [("Chicken Breast", 150), ("Brown Rice", 120), ("Broccoli", 100)],
    ),
    (
        "Salmon Quinoa Plate",
        "Salmon with quinoa and spinach for a balanced main meal.",
        "Dinner",
        [("Salmon", 120), ("Quinoa", 140), ("Spinach", 50)],
    ),
    (
        "Banana Yogurt Oats",
        "A quick breakfast with oats, yogurt, and fresh banana.",
        "Breakfast",
        [("Greek Yogurt", 150), ("Oats", 35), ("Banana", 70)],
    ),
    (
        "Lentil & Sweet Potato Bowl",
        "A plant-based combination of lentils, sweet potato, and greens.",
        "Lunch",
        [("Lentils", 150), ("Sweet Potato", 120), ("Spinach", 40)],
    ),
    (
        "Egg, Avocado & Spinach Plate",
        "A simple meal built around egg, avocado, and leafy greens.",
        "Breakfast",
        [("Whole Egg", 100), ("Avocado", 50), ("Spinach", 40)],
    ),
]


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS foods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                calories REAL NOT NULL,
                protein REAL NOT NULL,
                carbs REAL NOT NULL,
                fats REAL NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                external_code TEXT,
                source TEXT NOT NULL DEFAULT 'local'
            )
        """)
        food_columns = {row["name"] for row in db.execute("PRAGMA table_info(foods)")}
        if "external_code" not in food_columns:
            db.execute("ALTER TABLE foods ADD COLUMN external_code TEXT")
        if "source" not in food_columns:
            db.execute("ALTER TABLE foods ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_foods_external_code
            ON foods(external_code) WHERE external_code IS NOT NULL
        """)
        try:
            db.execute("ALTER TABLE users ADD COLUMN calorie_goal INTEGER DEFAULT 2000")
            db.commit()
        except Exception:
            pass
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "reminder_enabled" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN reminder_enabled INTEGER NOT NULL DEFAULT 0")
        if "reminder_time" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN reminder_time TEXT NOT NULL DEFAULT '20:00'")
        db.execute("""
            CREATE TABLE IF NOT EXISTS food_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                food_id INTEGER NOT NULL,
                grams REAL NOT NULL,
                meal_type TEXT NOT NULL DEFAULT 'Snack',
                date TEXT NOT NULL,
                logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                client_id TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (food_id) REFERENCES foods(id)
            )
        """)
        food_log_columns = {row["name"] for row in db.execute("PRAGMA table_info(food_logs)")}
        if "client_id" not in food_log_columns:
            db.execute("ALTER TABLE food_logs ADD COLUMN client_id TEXT")
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_food_logs_user_client_id
            ON food_logs(user_id, client_id) WHERE client_id IS NOT NULL
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS meal_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                food_id INTEGER NOT NULL,
                grams REAL NOT NULL CHECK (grams > 0 AND grams <= 2000),
                meal_type TEXT NOT NULL CHECK (meal_type IN ('Breakfast', 'Lunch', 'Dinner', 'Snack')),
                planned_date TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (food_id) REFERENCES foods(id)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_meal_plans_user_date
            ON meal_plans(user_id, planned_date)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL,
                meal_type TEXT NOT NULL,
                ingredient_count INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS recipe_ingredients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipe_id INTEGER NOT NULL,
                food_id INTEGER NOT NULL,
                grams REAL NOT NULL CHECK (grams > 0 AND grams <= 2000),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id),
                FOREIGN KEY (food_id) REFERENCES foods(id)
            )
        """)
        count = db.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
        if count == 0:
            db.executemany(
                "INSERT INTO foods (name, calories, protein, carbs, fats, category, description) VALUES (?,?,?,?,?,?,?)",
                SAMPLE_FOODS
            )
        recipe_count = db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        if recipe_count == 0:
            for name, description, meal_type, ingredients in SAMPLE_RECIPES:
                ingredient_rows = []
                for food_name, grams in ingredients:
                    food = db.execute(
                        "SELECT id FROM foods WHERE name = ? ORDER BY id LIMIT 1",
                        [food_name],
                    ).fetchone()
                    if not food:
                        ingredient_rows = []
                        break
                    ingredient_rows.append((food["id"], grams))
                if len(ingredient_rows) != len(ingredients):
                    continue
                cursor = db.execute(
                    "INSERT INTO recipes (name, description, meal_type, ingredient_count) VALUES (?, ?, ?, ?)",
                    [name, description, meal_type, len(ingredient_rows)],
                )
                db.executemany(
                    "INSERT INTO recipe_ingredients (recipe_id, food_id, grams) VALUES (?, ?, ?)",
                    [(cursor.lastrowid, food_id, grams) for food_id, grams in ingredient_rows],
                )
        db.commit()
        db.close()


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def home():
    db = get_db()
    featured = db.execute("SELECT * FROM foods ORDER BY RANDOM() LIMIT 6").fetchall()
    return render_template("home.html", featured=featured)


@app.route("/foods")
def foods():
    db = get_db()
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    barcode = request.args.get("barcode", "").strip()
    source = request.args.get("source", "local")
    if source not in {"local", "openfoodfacts"}:
        source = "local"
    sql = "SELECT * FROM foods WHERE 1=1"
    params = []
    if query:
        sql += " AND name LIKE ?"
        params.append(f"%{query}%")
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY name"
    all_foods = db.execute(sql, params).fetchall()
    categories = db.execute("SELECT DISTINCT category FROM foods ORDER BY category").fetchall()
    external_foods = []
    external_error = None
    if source == "openfoodfacts" and barcode:
        try:
            external_foods = [get_food_by_code(barcode)]
        except NutritionServiceError as exc:
            external_error = str(exc)
    elif source == "openfoodfacts" and query:
        if len(query) < 2:
            external_error = "Enter at least two characters to search the food database."
        else:
            try:
                external_foods = search_foods(query[:80])
            except NutritionServiceError as exc:
                external_error = str(exc)
    return render_template(
        "foods.html",
        foods=all_foods,
        categories=categories,
        query=query,
        selected_category=category,
        source=source,
        barcode=barcode,
        external_foods=external_foods,
        external_error=external_error,
    )


@app.route("/foods/import-openfoodfacts", methods=["POST"])
@login_required
def import_openfoodfacts_food():
    code = request.form.get("code", "").strip()
    db = get_db()
    existing = db.execute(
        "SELECT id FROM foods WHERE external_code = ?", [code]
    ).fetchone()
    if existing:
        flash("That product is already in your food database.", "info")
        return redirect(url_for("calculators", food_id=existing["id"]))

    try:
        product = get_food_by_code(code)
    except NutritionServiceError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("foods", q=request.form.get("q", ""), source="openfoodfacts"))

    nutrition = product["nutrition"]
    description = "Imported from Open Food Facts"
    if product["brands"]:
        description += f" • {product['brands']}"
    try:
        cursor = db.execute(
            """INSERT INTO foods
               (name, calories, protein, carbs, fats, category, description, external_code, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'openfoodfacts')""",
            [
                product["name"],
                nutrition["calories"],
                nutrition["protein"],
                nutrition["carbs"],
                nutrition["fats"],
                product["category"],
                description,
                product["code"],
            ],
        )
        db.commit()
    except sqlite3.IntegrityError:
        existing = db.execute(
            "SELECT id FROM foods WHERE external_code = ?", [product["code"]]
        ).fetchone()
        if existing:
            flash("That product is already in your food database.", "info")
            return redirect(url_for("calculators", food_id=existing["id"]))
        raise

    flash(f'"{product["name"]}" was added to your food database.', "success")
    return redirect(url_for("calculators", food_id=cursor.lastrowid))


@app.route("/calculators", methods=["GET", "POST"])
def calculators():
    db = get_db()
    all_foods = db.execute("SELECT * FROM foods ORDER BY name").fetchall()
    nutrition_result = None
    bmi_result = None
    selected_food_id = request.args.get("food_id", type=int)

    if request.method == "POST":
        calc_type = request.form.get("calc_type")

        if calc_type == "nutrition":
            food_id = request.form.get("food_id")
            grams = request.form.get("grams", type=float)
            if food_id and grams and grams > 0:
                food = db.execute("SELECT * FROM foods WHERE id = ?", [food_id]).fetchone()
                if food:
                    factor = grams / 100
                    nutrition_result = {
                        "food": food,
                        "grams": grams,
                        "calories": round(food["calories"] * factor, 1),
                        "protein": round(food["protein"] * factor, 1),
                        "carbs": round(food["carbs"] * factor, 1),
                        "fats": round(food["fats"] * factor, 1),
                    }
            else:
                flash("Please select a food and enter valid grams.", "danger")

        elif calc_type == "bmi":
            height = request.form.get("height", type=float)
            weight = request.form.get("weight", type=float)
            if height and weight and height > 0 and weight > 0:
                bmi = weight / ((height / 100) ** 2)
                bmi = round(bmi, 1)
                if bmi < 18.5:
                    category = "Underweight"
                    color = "text-blue-500"
                elif bmi < 25:
                    category = "Normal Weight"
                    color = "text-emerald-500"
                elif bmi < 30:
                    category = "Overweight"
                    color = "text-yellow-500"
                else:
                    category = "Obese"
                    color = "text-red-500"
                bmi_result = {"bmi": bmi, "category": category, "color": color, "height": height, "weight": weight}
            else:
                flash("Please enter valid height and weight values.", "danger")

    return render_template("calculators.html", foods=all_foods, nutrition_result=nutrition_result, bmi_result=bmi_result, selected_food_id=selected_food_id)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not username or not email or not password:
            flash("All fields are required.", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
        else:
            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE username=? OR email=?", [username, email]).fetchone()
            if existing:
                flash("Username or email already taken.", "danger")
            else:
                hashed = generate_password_hash(password)
                db.execute("INSERT INTO users (username, email, password) VALUES (?,?,?)", [username, email, hashed])
                db.commit()
                flash("Account created! Please log in.", "success")
                return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=? OR email=?", [identifier, identifier]).fetchone()
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["reminder_enabled"] = user["reminder_enabled"] if "reminder_enabled" in user.keys() else 0
            session["reminder_time"] = user["reminder_time"] if "reminder_time" in user.keys() else "20:00"
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username/email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    today = date.today().isoformat()
    total_foods = db.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    categories = db.execute("SELECT COUNT(DISTINCT category) FROM foods").fetchone()[0]
    all_foods = db.execute("SELECT * FROM foods ORDER BY name").fetchall()
    user = db.execute("SELECT calorie_goal FROM users WHERE id=?", [session["user_id"]]).fetchone()
    calorie_goal = user["calorie_goal"] if user and user["calorie_goal"] else 2000

    logs = db.execute("""
        SELECT fl.id, fl.grams, fl.meal_type, fl.logged_at,
               f.name, f.calories, f.protein, f.carbs, f.fats
        FROM food_logs fl
        JOIN foods f ON fl.food_id = f.id
        WHERE fl.user_id = ? AND fl.date = ?
        ORDER BY fl.logged_at ASC
    """, [session["user_id"], today]).fetchall()

    totals = {"calories": 0, "protein": 0, "carbs": 0, "fats": 0}
    for log in logs:
        factor = log["grams"] / 100
        totals["calories"] += log["calories"] * factor
        totals["protein"] += log["protein"] * factor
        totals["carbs"] += log["carbs"] * factor
        totals["fats"] += log["fats"] * factor
    totals = {k: round(v, 1) for k, v in totals.items()}

    return render_template("dashboard.html",
        total_foods=total_foods,
        categories=categories,
        all_foods=all_foods,
        logs=logs,
        totals=totals,
        today=today,
        calorie_goal=calorie_goal
    )


@app.route("/log-food", methods=["POST"])
@login_required
def log_food():
    from datetime import date, timedelta
    db = get_db()
    food_id = request.form.get("food_id")
    grams = request.form.get("grams", type=float)
    meal_type = request.form.get("meal_type", "Snack")
    today = date.today().isoformat()
    if not food_id or not grams or grams <= 0:
        flash("Please select a food and enter a valid amount.", "danger")
    else:
        food = db.execute("SELECT id FROM foods WHERE id=?", [food_id]).fetchone()
        if not food:
            flash("Food not found.", "danger")
        else:
            db.execute(
                "INSERT INTO food_logs (user_id, food_id, grams, meal_type, date) VALUES (?,?,?,?,?)",
                [session["user_id"], food_id, grams, meal_type, today]
            )
            db.commit()
            flash("Food logged successfully!", "success")
    return redirect(url_for("dashboard"))


@app.route("/log-food/delete/<int:log_id>", methods=["POST"])
@login_required
def delete_log(log_id):
    db = get_db()
    entry = db.execute("SELECT id FROM food_logs WHERE id=? AND user_id=?", [log_id, session["user_id"]]).fetchone()
    if entry:
        db.execute("DELETE FROM food_logs WHERE id=?", [log_id])
        db.commit()
        flash("Entry removed.", "info")
    return redirect(url_for("dashboard"))


@app.route("/sync-food-logs", methods=["POST"])
@login_required
def sync_food_logs():
    payload = request.get_json(silent=True)
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        return jsonify({"error": "Send between 1 and 100 queued food entries."}), 400

    validated = []
    seen_ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return jsonify({"error": "A queued entry has an invalid format."}), 400
        food_value = entry.get("food_id")
        grams_value = entry.get("grams")
        if isinstance(food_value, bool) or isinstance(grams_value, bool):
            return jsonify({"error": "A queued entry contains invalid values."}), 400
        try:
            client_id = str(uuid.UUID(str(entry.get("client_id", ""))))
            food_id = int(food_value)
            grams = float(grams_value)
            logged_date = date.fromisoformat(str(entry.get("date", "")))
        except (TypeError, ValueError, AttributeError):
            return jsonify({"error": "A queued entry contains invalid values."}), 400

        meal_type = entry.get("meal_type")
        if (
            client_id in seen_ids
            or not math.isfinite(grams)
            or grams <= 0
            or grams > 2000
            or not isinstance(meal_type, str)
            or meal_type not in {"Breakfast", "Lunch", "Dinner", "Snack"}
            or logged_date > date.today()
            or logged_date < date.today() - timedelta(days=365)
        ):
            return jsonify({"error": "A queued entry failed validation."}), 400
        seen_ids.add(client_id)
        validated.append((client_id, food_id, grams, meal_type, logged_date.isoformat()))

    db = get_db()
    for _, food_id, _, _, _ in validated:
        if not db.execute("SELECT id FROM foods WHERE id = ?", [food_id]).fetchone():
            return jsonify({"error": "A food in the queue is no longer available."}), 400

    accepted_ids = []
    for client_id, food_id, grams, meal_type, logged_date in validated:
        db.execute(
            """INSERT OR IGNORE INTO food_logs
               (user_id, food_id, grams, meal_type, date, client_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [session["user_id"], food_id, grams, meal_type, logged_date, client_id],
        )
        accepted_ids.append(client_id)
    db.commit()
    return jsonify({"accepted_ids": accepted_ids})


@app.route("/set-goal", methods=["POST"])
@login_required
def set_goal():
    goal = request.form.get("calorie_goal", type=int)
    if goal and 500 <= goal <= 10000:
        db = get_db()
        db.execute("UPDATE users SET calorie_goal=? WHERE id=?", [goal, session["user_id"]])
        db.commit()
        flash(f"Daily calorie goal set to {goal} kcal!", "success")
    else:
        flash("Please enter a goal between 500 and 10,000 kcal.", "danger")
    return redirect(url_for("dashboard"))


@app.route("/history")
@login_required
def history():
    db = get_db()
    user = db.execute("SELECT calorie_goal FROM users WHERE id=?", [session["user_id"]]).fetchone()
    calorie_goal = user["calorie_goal"] if user and user["calorie_goal"] else 2000

    today = date.today()
    requested_period = request.args.get("period", type=int)
    period = requested_period if requested_period in {7, 30, 90} else 7
    current_start = today - timedelta(days=period - 1)
    previous_start = current_start - timedelta(days=period)
    current_dates = [
        (current_start + timedelta(days=offset)).isoformat()
        for offset in range(period)
    ]
    previous_dates = [
        (previous_start + timedelta(days=offset)).isoformat()
        for offset in range(period)
    ]

    rows = db.execute("""
        SELECT fl.date, fl.grams, fl.meal_type,
               f.name, f.calories, f.protein, f.carbs, f.fats
        FROM food_logs fl
        JOIN foods f ON fl.food_id = f.id
        WHERE fl.user_id = ? AND fl.date BETWEEN ? AND ?
        ORDER BY fl.date ASC, fl.logged_at ASC
    """, [session["user_id"], previous_dates[0], today.isoformat()]).fetchall()

    current_days = {}
    previous_days = {}
    for d in current_dates:
        current_days[d] = {
            "date": d, "logs": [], "calories": 0, "protein": 0, "carbs": 0, "fats": 0
        }
    for d in previous_dates:
        previous_days[d] = {
            "date": d, "logs": [], "calories": 0, "protein": 0, "carbs": 0, "fats": 0
        }

    for row in rows:
        d = row["date"]
        if d in current_days:
            day_group = current_days
        elif d in previous_days:
            day_group = previous_days
        else:
            continue
        factor = row["grams"] / 100
        day_group[d]["logs"].append({
            "name": row["name"],
            "grams": row["grams"],
            "meal_type": row["meal_type"],
            "calories": round(row["calories"] * factor, 1),
            "protein": round(row["protein"] * factor, 1),
            "carbs": round(row["carbs"] * factor, 1),
            "fats": round(row["fats"] * factor, 1),
        })
        day_group[d]["calories"] = round(day_group[d]["calories"] + row["calories"] * factor, 1)
        day_group[d]["protein"] = round(day_group[d]["protein"] + row["protein"] * factor, 1)
        day_group[d]["carbs"] = round(day_group[d]["carbs"] + row["carbs"] * factor, 1)
        day_group[d]["fats"] = round(day_group[d]["fats"] + row["fats"] * factor, 1)

    current_day_list = [current_days[d] for d in current_dates]
    previous_day_list = [previous_days[d] for d in previous_dates]
    week_totals = {
        "calories": round(sum(day["calories"] for day in current_day_list), 1),
        "protein": round(sum(day["protein"] for day in current_day_list), 1),
        "carbs": round(sum(day["carbs"] for day in current_day_list), 1),
        "fats": round(sum(day["fats"] for day in current_day_list), 1),
        "avg_calories": round(sum(day["calories"] for day in current_day_list) / period, 1),
        "days_with_logs": sum(bool(day["logs"]) for day in current_day_list),
        "goal_days": sum(0 < day["calories"] <= calorie_goal for day in current_day_list),
    }
    previous_average = sum(day["calories"] for day in previous_day_list) / period
    week_totals["previous_avg_calories"] = round(previous_average, 1)
    week_totals["average_change_pct"] = (
        round((week_totals["avg_calories"] - previous_average) / previous_average * 100, 1)
        if previous_average > 0 else None
    )

    return render_template("history.html",
        days=current_day_list,
        week_totals=week_totals,
        calorie_goal=calorie_goal,
        today=today.isoformat(),
        period=period,
        periods=(7, 30, 90),
        start_date=current_dates[0],
        end_date=current_dates[-1],
    )


@app.route("/meal-plan")
@login_required
def meal_plan():
    db = get_db()
    requested_week = request.args.get("week", "")
    try:
        anchor = date.fromisoformat(requested_week) if requested_week else date.today()
    except ValueError:
        anchor = date.today()
    week_start = anchor - timedelta(days=anchor.weekday())
    week_dates = [week_start + timedelta(days=offset) for offset in range(7)]
    week_end = week_dates[-1]

    days = {
        day.isoformat(): {
            "date": day.isoformat(),
            "label": day.strftime("%A, %b %d").replace(" 0", " "),
            "is_today": day == date.today(),
            "entries": [],
            "totals": {"calories": 0, "protein": 0, "carbs": 0, "fats": 0},
        }
        for day in week_dates
    }
    rows = db.execute(
        """SELECT mp.id, mp.food_id, mp.grams, mp.meal_type, mp.planned_date,
                  f.name, f.calories, f.protein, f.carbs, f.fats
           FROM meal_plans mp
           JOIN foods f ON f.id = mp.food_id
           WHERE mp.user_id = ? AND mp.planned_date BETWEEN ? AND ?
           ORDER BY mp.planned_date, mp.meal_type, f.name""",
        [session["user_id"], week_dates[0].isoformat(), week_end.isoformat()],
    ).fetchall()

    shopping_by_food = {}
    for row in rows:
        factor = row["grams"] / 100
        nutrition = {
            "calories": round(row["calories"] * factor, 1),
            "protein": round(row["protein"] * factor, 1),
            "carbs": round(row["carbs"] * factor, 1),
            "fats": round(row["fats"] * factor, 1),
        }
        entry = {
            "id": row["id"],
            "food_id": row["food_id"],
            "name": row["name"],
            "grams": row["grams"],
            "meal_type": row["meal_type"],
            **nutrition,
        }
        day = days[row["planned_date"]]
        day["entries"].append(entry)
        for key, value in nutrition.items():
            day["totals"][key] = round(day["totals"][key] + value, 1)

        if row["food_id"] not in shopping_by_food:
            shopping_by_food[row["food_id"]] = {"name": row["name"], "grams": 0}
        shopping_by_food[row["food_id"]]["grams"] += row["grams"]

    week_totals = {
        key: round(sum(day["totals"][key] for day in days.values()), 1)
        for key in ("calories", "protein", "carbs", "fats")
    }
    foods_for_planning = db.execute("SELECT id, name FROM foods ORDER BY name").fetchall()
    return render_template(
        "meal_plan.html",
        days=list(days.values()),
        foods=foods_for_planning,
        week_start=week_dates[0].isoformat(),
        previous_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat(),
        week_label=f"{week_dates[0].strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}",
        week_totals=week_totals,
        shopping_list=sorted(
            [
                {"name": item["name"], "grams": round(item["grams"], 1)}
                for item in shopping_by_food.values()
            ],
            key=lambda item: item["name"].casefold(),
        ),
        meal_types=("Breakfast", "Lunch", "Dinner", "Snack"),
    )


@app.route("/meal-plan/add", methods=["POST"])
@login_required
def add_meal_plan_entry():
    db = get_db()
    food_id = request.form.get("food_id", type=int)
    grams = request.form.get("grams", type=float)
    meal_type = request.form.get("meal_type", "")
    planned_date_value = request.form.get("planned_date", "")
    week_value = request.form.get("week", "")
    try:
        selected_date = date.fromisoformat(planned_date_value)
        week_anchor = date.fromisoformat(week_value)
    except ValueError:
        flash("Choose a valid date for the planned meal.", "danger")
        return redirect(url_for("meal_plan"))

    week_start = week_anchor - timedelta(days=week_anchor.weekday())
    week_end = week_start + timedelta(days=6)
    valid_food = db.execute("SELECT id FROM foods WHERE id = ?", [food_id]).fetchone() if food_id else None
    if not valid_food:
        flash("Choose a food from the list.", "danger")
    elif grams is None or not math.isfinite(grams) or grams <= 0 or grams > 2000:
        flash("Enter an amount between 1 and 2,000 grams.", "danger")
    elif meal_type not in {"Breakfast", "Lunch", "Dinner", "Snack"}:
        flash("Choose a valid meal type.", "danger")
    elif not (week_start <= selected_date <= week_end):
        flash("The planned date must be within the displayed week.", "danger")
    else:
        db.execute(
            """INSERT INTO meal_plans (user_id, food_id, grams, meal_type, planned_date)
               VALUES (?, ?, ?, ?, ?)""",
            [session["user_id"], food_id, grams, meal_type, selected_date.isoformat()],
        )
        db.commit()
        flash("Meal added to your weekly plan.", "success")
    return redirect(url_for("meal_plan", week=week_start.isoformat()))


@app.route("/meal-plan/delete/<int:plan_id>", methods=["POST"])
@login_required
def delete_meal_plan_entry(plan_id):
    db = get_db()
    week_value = request.form.get("week", "")
    try:
        week_anchor = date.fromisoformat(week_value)
        week_start = week_anchor - timedelta(days=week_anchor.weekday())
    except ValueError:
        week_start = date.today() - timedelta(days=date.today().weekday())

    cursor = db.execute(
        "DELETE FROM meal_plans WHERE id = ? AND user_id = ?",
        [plan_id, session["user_id"]],
    )
    db.commit()
    if cursor.rowcount:
        flash("Planned meal removed.", "info")
    return redirect(url_for("meal_plan", week=week_start.isoformat()))


@app.route("/recipes")
@login_required
def recipes():
    db = get_db()
    today = date.today()
    user = db.execute(
        "SELECT calorie_goal FROM users WHERE id = ?", [session["user_id"]]
    ).fetchone()
    calorie_goal = user["calorie_goal"] if user and user["calorie_goal"] else 2000
    logged = db.execute(
        """SELECT SUM(f.calories * fl.grams / 100.0) AS calories
           FROM food_logs fl JOIN foods f ON f.id = fl.food_id
           WHERE fl.user_id = ? AND fl.date = ?""",
        [session["user_id"], today.isoformat()],
    ).fetchone()
    consumed_calories = round(logged["calories"] or 0, 1)
    remaining_calories = max(0, round(calorie_goal - consumed_calories, 1))

    rows = db.execute(
        """SELECT r.id AS recipe_id, r.name, r.description, r.meal_type,
                  r.ingredient_count, ri.grams,
                  f.id AS food_id, f.name AS food_name,
                  f.calories, f.protein, f.carbs, f.fats
           FROM recipes r
           JOIN recipe_ingredients ri ON ri.recipe_id = r.id
           JOIN foods f ON f.id = ri.food_id
           ORDER BY r.name, f.name"""
    ).fetchall()
    by_recipe = {}
    for row in rows:
        recipe = by_recipe.setdefault(
            row["recipe_id"],
            {
                "id": row["recipe_id"],
                "name": row["name"],
                "description": row["description"],
                "meal_type": row["meal_type"],
                "ingredient_count": row["ingredient_count"],
                "ingredients": [],
                "calories": 0,
                "protein": 0,
                "carbs": 0,
                "fats": 0,
            },
        )
        factor = row["grams"] / 100
        recipe["ingredients"].append(
            {"food_id": row["food_id"], "name": row["food_name"], "grams": row["grams"]}
        )
        for key in ("calories", "protein", "carbs", "fats"):
            recipe[key] += row[key] * factor

    available_recipes = []
    for recipe in by_recipe.values():
        if len(recipe["ingredients"]) != recipe["ingredient_count"]:
            continue
        for key in ("calories", "protein", "carbs", "fats"):
            recipe[key] = round(recipe[key], 1)
        recipe["fits_remaining"] = recipe["calories"] <= remaining_calories
        available_recipes.append(recipe)

    available_recipes.sort(
        key=lambda recipe: (
            not recipe["fits_remaining"],
            abs(remaining_calories - recipe["calories"]),
            recipe["name"].casefold(),
        )
    )
    return render_template(
        "recipes.html",
        recipes=available_recipes,
        calorie_goal=calorie_goal,
        consumed_calories=consumed_calories,
        remaining_calories=remaining_calories,
        today=today.isoformat(),
    )


@app.route("/recipes/plan/<int:recipe_id>", methods=["POST"])
@login_required
def add_recipe_to_meal_plan(recipe_id):
    planned_date_value = request.form.get("planned_date", "")
    try:
        planned_date = date.fromisoformat(planned_date_value)
    except ValueError:
        flash("Choose a valid date for the recipe.", "danger")
        return redirect(url_for("recipes"))

    db = get_db()
    recipe = db.execute("SELECT id, meal_type FROM recipes WHERE id = ?", [recipe_id]).fetchone()
    ingredients = db.execute(
        "SELECT food_id, grams FROM recipe_ingredients WHERE recipe_id = ?",
        [recipe_id],
    ).fetchall()
    if not recipe or not ingredients:
        flash("That recipe is no longer available.", "danger")
        return redirect(url_for("recipes"))
    db.executemany(
        """INSERT INTO meal_plans (user_id, food_id, grams, meal_type, planned_date)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (session["user_id"], item["food_id"], item["grams"], recipe["meal_type"], planned_date.isoformat())
            for item in ingredients
        ],
    )
    db.commit()
    flash("Recipe ingredients were added to your meal plan.", "success")
    week_start = planned_date - timedelta(days=planned_date.weekday())
    return redirect(url_for("meal_plan", week=week_start.isoformat()))


@app.route("/export-food-log.csv")
@login_required
def export_food_log():
    db = get_db()
    rows = db.execute(
        """SELECT fl.date, fl.meal_type, fl.grams, f.name,
                  f.calories, f.protein, f.carbs, f.fats
           FROM food_logs fl
           JOIN foods f ON f.id = fl.food_id
           WHERE fl.user_id = ?
           ORDER BY fl.date DESC, fl.logged_at DESC""",
        [session["user_id"]],
    ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Date", "Meal", "Food", "Grams", "Calories (kcal)", "Protein (g)", "Carbs (g)", "Fat (g)"])
    for row in rows:
        factor = row["grams"] / 100
        food_name = str(row["name"] or "")
        if food_name.lstrip().startswith(("=", "+", "-", "@")):
            food_name = "'" + food_name
        meal_type = str(row["meal_type"] or "")
        if meal_type.lstrip().startswith(("=", "+", "-", "@")):
            meal_type = "'" + meal_type
        writer.writerow([
            row["date"],
            meal_type,
            food_name,
            round(row["grams"], 1),
            round(row["calories"] * factor, 1),
            round(row["protein"] * factor, 1),
            round(row["carbs"] * factor, 1),
            round(row["fats"] * factor, 1),
        ])

    response = Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nutritrack-food-log.csv"'},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    confirmation = request.form.get("confirmation", "").strip()
    password = request.form.get("password", "")
    if confirmation != "DELETE":
        flash('Type "DELETE" to confirm account deletion.', "danger")
        return redirect(url_for("profile"))

    db = get_db()
    user = db.execute(
        "SELECT password FROM users WHERE id = ?", [session["user_id"]]
    ).fetchone()
    if not user or not check_password_hash(user["password"], password):
        flash("Your password is incorrect. Your account was not deleted.", "danger")
        return redirect(url_for("profile"))

    user_id = session["user_id"]
    db.execute("DELETE FROM food_logs WHERE user_id = ?", [user_id])
    db.execute("DELETE FROM meal_plans WHERE user_id = ?", [user_id])
    db.execute("DELETE FROM users WHERE id = ?", [user_id])
    db.commit()
    session.clear()
    session["offline_cleanup_user_id"] = str(user_id)
    flash("Your account, food logs, and meal plans have been deleted.", "info")
    return redirect(url_for("home"))


@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(
        os.path.join(app.root_path, "static"),
        "service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = db.execute(
        "SELECT id, username, email, reminder_enabled, reminder_time FROM users WHERE id=?",
        [session["user_id"]],
    ).fetchone()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            new_username = request.form.get("username", "").strip()
            new_email = request.form.get("email", "").strip()
            if not new_username or not new_email:
                flash("Username and email are required.", "danger")
            else:
                conflict = db.execute(
                    "SELECT id FROM users WHERE (username=? OR email=?) AND id!=?",
                    [new_username, new_email, session["user_id"]]
                ).fetchone()
                if conflict:
                    flash("That username or email is already taken.", "danger")
                else:
                    db.execute("UPDATE users SET username=?, email=? WHERE id=?",
                               [new_username, new_email, session["user_id"]])
                    db.commit()
                    session["username"] = new_username
                    flash("Profile updated successfully!", "success")
                    return redirect(url_for("profile"))

        elif action == "change_password":
            current = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            full_user = db.execute("SELECT password FROM users WHERE id=?", [session["user_id"]]).fetchone()
            if not check_password_hash(full_user["password"], current):
                flash("Current password is incorrect.", "danger")
            elif len(new_pw) < 6:
                flash("New password must be at least 6 characters.", "danger")
            elif new_pw != confirm:
                flash("New passwords do not match.", "danger")
            else:
                db.execute("UPDATE users SET password=? WHERE id=?",
                           [generate_password_hash(new_pw), session["user_id"]])
                db.commit()
                flash("Password changed successfully!", "success")
                return redirect(url_for("profile"))

        elif action == "update_reminders":
            reminder_enabled = 1 if request.form.get("reminder_enabled") == "1" else 0
            reminder_time = request.form.get("reminder_time", "").strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder_time):
                flash("Choose a valid reminder time.", "danger")
            else:
                db.execute(
                    "UPDATE users SET reminder_enabled=?, reminder_time=? WHERE id=?",
                    [reminder_enabled, reminder_time, session["user_id"]],
                )
                db.commit()
                session["reminder_enabled"] = reminder_enabled
                session["reminder_time"] = reminder_time
                flash("Reminder preferences saved.", "success")
                return redirect(url_for("profile"))

    return render_template("profile.html", user=user)


@app.route("/manage-foods")
@login_required
def manage_foods():
    db = get_db()
    all_foods = db.execute("SELECT * FROM foods ORDER BY category, name").fetchall()
    categories = db.execute("SELECT DISTINCT category FROM foods ORDER BY category").fetchall()
    return render_template("manage_foods.html", foods=all_foods, categories=categories)


@app.route("/manage-foods/add", methods=["GET", "POST"])
@login_required
def add_food():
    db = get_db()
    categories = db.execute("SELECT DISTINCT category FROM foods ORDER BY category").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        calories = request.form.get("calories", type=float)
        protein = request.form.get("protein", type=float)
        carbs = request.form.get("carbs", type=float)
        fats = request.form.get("fats", type=float)
        category = request.form.get("category", "").strip()
        new_category = request.form.get("new_category", "").strip()
        description = request.form.get("description", "").strip()

        if new_category:
            category = new_category

        if not name or calories is None or protein is None or carbs is None or fats is None or not category:
            flash("All fields except description are required.", "danger")
        else:
            db.execute(
                "INSERT INTO foods (name, calories, protein, carbs, fats, category, description) VALUES (?,?,?,?,?,?,?)",
                [name, calories, protein, carbs, fats, category, description]
            )
            db.commit()
            flash(f'"{name}" has been added to the food database!', "success")
            return redirect(url_for("manage_foods"))
    return render_template("add_food.html", categories=categories)


@app.route("/manage-foods/edit/<int:food_id>", methods=["GET", "POST"])
@login_required
def edit_food(food_id):
    db = get_db()
    food = db.execute("SELECT * FROM foods WHERE id = ?", [food_id]).fetchone()
    if not food:
        flash("Food not found.", "danger")
        return redirect(url_for("manage_foods"))
    categories = db.execute("SELECT DISTINCT category FROM foods ORDER BY category").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        calories = request.form.get("calories", type=float)
        protein = request.form.get("protein", type=float)
        carbs = request.form.get("carbs", type=float)
        fats = request.form.get("fats", type=float)
        category = request.form.get("category", "").strip()
        new_category = request.form.get("new_category", "").strip()
        description = request.form.get("description", "").strip()

        if new_category:
            category = new_category

        if not name or calories is None or protein is None or carbs is None or fats is None or not category:
            flash("All fields except description are required.", "danger")
        else:
            db.execute(
                "UPDATE foods SET name=?, calories=?, protein=?, carbs=?, fats=?, category=?, description=? WHERE id=?",
                [name, calories, protein, carbs, fats, category, description, food_id]
            )
            db.commit()
            flash(f'"{name}" has been updated successfully!', "success")
            return redirect(url_for("manage_foods"))
    return render_template("edit_food.html", food=food, categories=categories)


@app.route("/manage-foods/delete/<int:food_id>", methods=["POST"])
@login_required
def delete_food(food_id):
    db = get_db()
    food = db.execute("SELECT name FROM foods WHERE id = ?", [food_id]).fetchone()
    if food:
        db.execute("DELETE FROM foods WHERE id = ?", [food_id])
        db.commit()
        flash(f'"{food["name"]}" has been deleted.', "info")
    else:
        flash("Food not found.", "danger")
    return redirect(url_for("manage_foods"))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

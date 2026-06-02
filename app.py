# ===========================
# app.py
# ===========================

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from flasgger import Swagger

from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
import google.auth.transport.requests
import requests

from apscheduler.schedulers.background import BackgroundScheduler

from textblob import TextBlob

import qrcode
import os

from datetime import datetime, timedelta

import config

app = Flask(__name__)

# ================= CONFIG =================

app.config.from_object(config)

app.secret_key = "supersecretkey"

mysql = MySQL(app)

bcrypt = Bcrypt(app)

mail = Mail(app)

Swagger(app)

# ================= GOOGLE OAUTH =================

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

GOOGLE_CLIENT_SECRETS_FILE = "client_secret.json"

flow = Flow.from_client_secrets_file(

    GOOGLE_CLIENT_SECRETS_FILE,

    scopes=[
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "openid"
    ],

    redirect_uri="http://localhost:5000/login/google/callback"
)

# ================= MAIL =================

def send_simple_mail(to_email, subject, body):

    try:

        msg = Message(
            subject,
            sender=app.config['MAIL_USERNAME'],
            recipients=[to_email]
        )

        msg.body = body

        mail.send(msg)

    except Exception as e:

        print("EMAIL ERROR:", e)

# ================= ROLE =================

def get_role(email):

    if email.endswith("@student.usv.ro"):
        return "student"

    elif email.endswith("@usm.ro"):
        return "profesor"

    elif email.endswith("@admin.usv.ro"):
        return "admin"

    return None

# ================= AUTH =================

@app.route('/')
def login_page():
    return render_template("login.html")

@app.route('/register_page')
def register_page():
    return render_template("register.html")

# ================= REGISTER =================

@app.route('/register', methods=['POST'])
def register():

    data = request.json

    email = data.get("email")

    password = data.get("password")

    role = get_role(email)

    if not role:
        return jsonify({"error":"Email invalid"}),400

    hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')

    cur = mysql.connection.cursor()

    try:

        cur.execute("""

            INSERT INTO users(email,password,role)

            VALUES(%s,%s,%s)

        """,(email,hashed_pw,role))

        mysql.connection.commit()

    except:

        return jsonify({"error":"User existent"}),400

    finally:

        cur.close()

    return jsonify({"message":"Cont creat cu succes"})

# ================= LOGIN =================

@app.route('/login', methods=['POST'])
def login():

    data = request.json

    email = data.get("email")

    password = data.get("password")

    cur = mysql.connection.cursor()

    cur.execute("""

        SELECT id,email,password,role

        FROM users

        WHERE email=%s

    """,(email,))

    user = cur.fetchone()

    cur.close()

    if not user:
        return jsonify({"error":"Date greșite"}),401

    if not bcrypt.check_password_hash(user[2], password):
        return jsonify({"error":"Date greșite"}),401

    session["user"] = user[1]

    session["role"] = user[3]

    send_simple_mail(
        email,
        "Login detectat",
        "Te-ai logat în platforma USV Events."
    )

    return jsonify({"message":"Login reușit"})

# ================= GOOGLE LOGIN =================

@app.route("/login/google")
def login_google():

    authorization_url, state = flow.authorization_url()

    session["state"] = state

    return redirect(authorization_url)

@app.route("/login/google/callback")
def callback_google():

    if "state" not in session:
        return redirect("/")

    if session["state"] != request.args.get("state"):
        return redirect("/")

    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials

    request_session = requests.Session()

    token_request = google.auth.transport.requests.Request(
        session=request_session
    )

    id_info = id_token.verify_oauth2_token(
        credentials._id_token,
        token_request,
        flow.client_config["client_id"]
    )

    email = id_info.get("email")

    if not email.endswith("@student.usv.ro"):

        return "Doar emailurile @student.usv.ro sunt acceptate!"

    cur = mysql.connection.cursor()

    cur.execute("""

        SELECT id,email,role

        FROM users

        WHERE email=%s

    """,(email,))

    user = cur.fetchone()

    if not user:

        cur.execute("""

            INSERT INTO users(email,password,role)

            VALUES(%s,%s,%s)

        """,(email,"","student"))

        mysql.connection.commit()

    cur.close()

    session["user"] = email

    session["role"] = "student"

    return redirect("/dashboard")

# ================= DASHBOARD =================

@app.route('/dashboard')
def dashboard():

    if "user" not in session:
        return redirect("/")

    email = session["user"]

    initials = "".join([
        p[0].upper()
        for p in email.split("@")[0].split(".")
    ][:2])

    return render_template(
        "dashboard.html",
        initials=initials
    )

# ================= EVENTS API =================

@app.route('/api/events')
def get_events():

    cur = mysql.connection.cursor()

    faculty = request.args.get("faculty")
    date = request.args.get("date")
    category = request.args.get("category")
    location = request.args.get("location")
    organizer = request.args.get("organizer")
    mode = request.args.get("mode")
    has_qr = request.args.get("has_qr")
    requires_registration = request.args.get("requires_registration")
    sort = request.args.get("sort", "date_asc")

    query = """

    SELECT
        e.id,
        e.title,
        e.description,
        e.start_datetime,
        e.end_datetime,
        e.location,
        e.faculty,
        e.category,
        e.organizer,
        e.mode,
        e.registration_link,
        e.has_qr,
        e.requires_registration,

        EXISTS(

            SELECT 1

            FROM registrations r

            WHERE r.event_id=e.id

            AND r.user_email=%s

        ) AS is_registered

    FROM events e

    WHERE 1=1

    """

    params = [
        session["user"]
        if "user" in session
        else ""
    ]

    # ================= FILTERS =================

    if faculty:
        query += " AND e.faculty=%s"
        params.append(faculty)

    if date:
        query += " AND DATE(e.start_datetime)=%s"
        params.append(date)

    if category:
        query += " AND e.category=%s"
        params.append(category)

    if location:
        query += " AND e.location LIKE %s"
        params.append(f"%{location}%")

    if organizer:
        query += " AND e.organizer LIKE %s"
        params.append(f"%{organizer}%")

    if mode:
        query += " AND e.mode=%s"
        params.append(mode)

    if has_qr:
        query += " AND e.has_qr=%s"
        params.append(has_qr)

    if requires_registration:
        query += " AND e.requires_registration=%s"
        params.append(requires_registration)

    # ================= SORT =================

    if sort == "date_desc":
        query += " ORDER BY e.start_datetime DESC"

    else:
        query += " ORDER BY e.start_datetime ASC"

    cur.execute(query, tuple(params))

    rows = cur.fetchall()

    cur.close()

    result = []

    for r in rows:

        result.append({

            "id": r[0],
            "title": r[1],
            "description": r[2],

            "start_datetime":
                r[3].strftime("%Y-%m-%d %H:%M"),

            "end_datetime":
                r[4].strftime("%Y-%m-%d %H:%M")
                if r[4] else None,

            "location": r[5],
            "faculty": r[6],
            "category": r[7],
            "organizer": r[8],
            "mode": r[9],
            "registration_link": r[10],

            "has_qr": bool(r[11]),

            "requires_registration":
                bool(r[12]),

            "is_registered":
                bool(r[13])

        })

    return jsonify(result)
# ================= REGISTER EVENT =================

@app.route('/api/events/<int:event_id>/register', methods=['POST'])
def register_event(event_id):

    if "user" not in session:
        return jsonify({"error":"Login necesar"}),401

    user_email = session["user"]

    cur = mysql.connection.cursor()

    cur.execute("""

        SELECT COUNT(*)

        FROM registrations

        WHERE event_id=%s

    """,(event_id,))

    participants = cur.fetchone()[0]

    cur.execute("""

        SELECT max_participants,title

        FROM events

        WHERE id=%s

    """,(event_id,))

    event = cur.fetchone()

    max_participants = event[0]

    title = event[1]

    if participants >= max_participants:

        cur.execute("""

            INSERT INTO waitlist(event_id,user_email)

            VALUES(%s,%s)

        """,(event_id,user_email))

        mysql.connection.commit()

        return jsonify({
            "message":"Adăugat în lista de așteptare"
        })

    cur.execute("""

        INSERT INTO registrations(event_id,user_email)

        VALUES(%s,%s)

    """,(event_id,user_email))

    mysql.connection.commit()

    qr = qrcode.make(
        f"{user_email}-{event_id}"
    )

    if not os.path.exists("static/tickets"):
        os.makedirs("static/tickets")

    qr_path = f"static/tickets/{user_email}_{event_id}.png"

    qr.save(qr_path)

    send_simple_mail(
        user_email,
        "Înscriere confirmată",
        f"Te-ai înscris la {title}"
    )

    cur.close()

    return jsonify({
        "message":"Înscriere realizată",
        "ticket":qr_path
    })

# ================= UNREGISTER =================

@app.route('/api/events/<int:event_id>/unregister', methods=['POST'])
def unregister_event(event_id):

    if "user" not in session:
        return jsonify({"error":"Login necesar"}),401

    user_email = session["user"]

    cur = mysql.connection.cursor()

    cur.execute("""

        DELETE FROM registrations

        WHERE event_id=%s
        AND user_email=%s

    """,(event_id,user_email))

    mysql.connection.commit()

    cur.close()

    return jsonify({
        "message":"Înscriere anulată"
    })

# ================= FEEDBACK =================

@app.route('/api/events/<int:event_id>/feedback', methods=['POST'])
def feedback(event_id):

    if "user" not in session:
        return jsonify({"error":"Login necesar"}),401

    data = request.json

    rating = data.get("rating")

    comment = data.get("comment")

    sentiment = TextBlob(comment).sentiment.polarity

    cur = mysql.connection.cursor()

    cur.execute("""

        INSERT INTO feedback(
            event_id,
            user_email,
            rating,
            comment,
            sentiment
        )

        VALUES(%s,%s,%s,%s,%s)

    """,(
        event_id,
        session["user"],
        rating,
        comment,
        sentiment
    ))

    mysql.connection.commit()

    cur.close()

    return jsonify({
        "message":"Feedback salvat",
        "sentiment":sentiment
    })

# ================= RECOMMENDATIONS =================

@app.route('/api/recommendations')
def recommendations():

    if "user" not in session:
        return jsonify([])

    user_email = session["user"]

    cur = mysql.connection.cursor()

    cur.execute("""

        SELECT DISTINCT category

        FROM registrations r

        JOIN events e
        ON r.event_id=e.id

        WHERE r.user_email=%s

    """,(user_email,))

    categories = [x[0] for x in cur.fetchall()]

    if not categories:
        return jsonify([])

    query = """

        SELECT id,title,category

        FROM events

        WHERE category IN (%s)

    """ % ",".join(["%s"] * len(categories))

    cur.execute(query, categories)

    rows = cur.fetchall()

    cur.close()

    return jsonify([
        {
            "id":r[0],
            "title":r[1],
            "category":r[2]
        }
        for r in rows
    ])

# ================= EXPORT ICS =================

@app.route('/api/events/<int:event_id>/ics')
def export_ics(event_id):

    cur = mysql.connection.cursor()

    cur.execute("""

        SELECT
            title,
            description,
            start_datetime,
            end_datetime,
            location

        FROM events

        WHERE id=%s

    """,(event_id,))

    e = cur.fetchone()

    cur.close()

    if not e:
        return "Not found",404

    title,desc,start,end,location = e

    content = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{desc}
DTSTART:{start.strftime('%Y%m%dT%H%M%S')}
DTEND:{end.strftime('%Y%m%dT%H%M%S')}
LOCATION:{location}
END:VEVENT
END:VCALENDAR
"""

    return Response(
        content,
        mimetype="text/calendar",
        headers={
            "Content-Disposition":
            f"attachment; filename=event_{event_id}.ics"
        }
    )

# ================= REMINDERS =================

scheduler = BackgroundScheduler()

def send_reminders():

    cur = mysql.connection.cursor()

    tomorrow = datetime.now() + timedelta(days=1)

    cur.execute("""

        SELECT
            users.email,
            events.title

        FROM registrations

        JOIN users
        ON registrations.user_email=users.email

        JOIN events
        ON registrations.event_id=events.id

        WHERE DATE(events.start_datetime)=%s

    """,(tomorrow.strftime("%Y-%m-%d"),))

    rows = cur.fetchall()

    for r in rows:

        send_simple_mail(
            r[0],
            "Reminder eveniment",
            f"Mâine participi la {r[1]}"
        )

    cur.close()

scheduler.add_job(
    send_reminders,
    'interval',
    hours=24
)

scheduler.start()

# ================= LOGOUT =================

@app.route('/logout')
def logout():

    session.clear()

    return redirect("/")

# ================= RUN =================

if __name__ == '__main__':

    app.run(
        debug=True,
        host="localhost",
        port=5000
    )
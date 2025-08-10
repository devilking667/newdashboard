import os
import threading
import time
import sqlite3
import uuid
from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, send_from_directory, flash
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecretkey"  # Needed for flash messages
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_PATH = os.path.join(os.path.dirname(__file__), 'dashboard.db')

# ---------- Database helpers ----------

def get_db():
    db = getattr(app, '_database', None)
    if db is None:
        need_init = not os.path.exists(DB_PATH)
        db = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
        app._database = db
        if need_init:
            init_db(db)
    return db

def init_db(db):
    c = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS bots (
        id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        active INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        bot_id TEXT,
        username TEXT,
        password TEXT,
        status TEXT DEFAULT 'offline',
        logged_in INTEGER DEFAULT 0,
        FOREIGN KEY(bot_id) REFERENCES bots(id)
    );
    CREATE TABLE IF NOT EXISTS activity (
        id TEXT PRIMARY KEY,
        ts TEXT,
        bot_id TEXT,
        account_id TEXT,
        type TEXT,
        message TEXT
    );
    CREATE TABLE IF NOT EXISTS targets (
        id TEXT PRIMARY KEY,
        bot_id TEXT,
        account_id TEXT,
        type TEXT,
        payload TEXT,
        processed INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS dm_templates (
        id TEXT PRIMARY KEY,
        bot_id TEXT,
        account_id TEXT,
        message TEXT
    );
    """)
    db.commit()

def query_all(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]

def query_one(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None

def execute(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.lastrowid

# ---------- Bot Worker (simplified rotation example) ----------

bot_threads = {}
bot_running = {}

def bot_worker(bot_id):
    while bot_running.get(bot_id, False):
        accounts = query_all("SELECT * FROM accounts WHERE bot_id=?", (bot_id,))
        if not accounts:
            time.sleep(10)
            continue
        running_acc = next((a for a in accounts if a['status'] == 'running'), None)
        idx = accounts.index(running_acc) if running_acc else -1
        next_idx = (idx + 1) % len(accounts)
        db = get_db()
        cur = db.cursor()
        for i, acc in enumerate(accounts):
            status = 'running' if i == next_idx else 'idle'
            cur.execute("UPDATE accounts SET status=? WHERE id=?", (status, acc['id']))
        db.commit()
        time.sleep(20 * 60)  # 20 minutes rotation

@app.route('/')
def index():
    bots = query_all("SELECT * FROM bots")
    return render_template('index.html', bots=bots)

@app.route('/bot/start/<bot_id>', methods=['POST'])
def start_bot(bot_id):
    if bot_running.get(bot_id):
        return redirect(url_for('index'))
    bot_running[bot_id] = True
    t = threading.Thread(target=bot_worker, args=(bot_id,), daemon=True)
    t.start()
    bot_threads[bot_id] = t
    execute("UPDATE bots SET active=1 WHERE id=?", (bot_id,))
    return redirect(url_for('index'))

@app.route('/bot/stop/<bot_id>', methods=['POST'])
def stop_bot(bot_id):
    bot_running[bot_id] = False
    execute("UPDATE bots SET active=0 WHERE id=?", (bot_id,))
    return redirect(url_for('index'))

# ---------- Static files route ----------

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------- Run ----------

@app.route('/bot/<bot_id>')
def bot_detail(bot_id):
    bot = query_one("SELECT * FROM bots WHERE id=?", (bot_id,))
    if not bot:
        flash("Bot not found.")
        return redirect(url_for('index'))
    accounts = query_all("SELECT * FROM accounts WHERE bot_id=?", (bot_id,))
    dm_templates = query_all("SELECT * FROM dm_templates WHERE bot_id=?", (bot_id,))
    return render_template('bots.html', bot=bot, accounts=accounts, dm_templates=dm_templates)

@app.route('/bot/<bot_id>/upload_targets', methods=['GET', 'POST'])
def upload_targets(bot_id):
    bot = query_one("SELECT * FROM bots WHERE id=?", (bot_id,))
    if not bot:
        flash("Bot not found.")
        return redirect(url_for('index'))

    if request.method == 'POST':
        target_type = request.form.get('target_type')
        db = get_db()
        cur = db.cursor()

        if target_type == 'follow_users':
            # Handle file upload
            if 'target_file' not in request.files:
                flash("No file part")
                return redirect(request.url)
            file = request.files['target_file']
            if file.filename == '':
                flash("No selected file")
                return redirect(request.url)

            filename = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            # Read usernames from file
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            with open(path, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]

            # Insert targets for each account of bot
            accounts = query_all("SELECT * FROM accounts WHERE bot_id=?", (bot_id,))
            for acc in accounts:
                for username in lines:
                    tid = uuid.uuid4().hex
                    cur.execute("INSERT INTO targets (id, bot_id, account_id, type, payload, processed) VALUES (?, ?, ?, ?, ?, 0)",
                                (tid, bot_id, acc['id'], 'follow_user', username))
            db.commit()
            flash(f"Uploaded {len(lines)} usernames for following.")
            return redirect(url_for('bot_detail', bot_id=bot_id))

        elif target_type == 'follow_likers':
            reel_url = request.form.get('reel_url')
            if not reel_url:
                flash("Reel URL is required")
                return redirect(request.url)
            accounts = query_all("SELECT * FROM accounts WHERE bot_id=?", (bot_id,))
            for acc in accounts:
                tid = uuid.uuid4().hex
                cur.execute("INSERT INTO targets (id, bot_id, account_id, type, payload, processed) VALUES (?, ?, ?, ?, ?, 0)",
                            (tid, bot_id, acc['id'], 'follow_liker', reel_url))
            db.commit()
            flash("Reel likers targets added.")
            return redirect(url_for('bot_detail', bot_id=bot_id))

    return render_template('upload.html', bot=bot)


@app.route('/bot/<bot_id>/dm_templates', methods=['GET'])
def dm_templates(bot_id):
    bot = query_one("SELECT * FROM bots WHERE id=?", (bot_id,))
    if not bot:
        flash("Bot not found.")
        return redirect(url_for('index'))
    dms = query_all("SELECT * FROM dm_templates WHERE bot_id=?", (bot_id,))
    return render_template('dm_templates.html', bot=bot, dm_templates=dms)

@app.route('/bot/<bot_id>/dm_templates/add', methods=['POST'])
def add_dm_template(bot_id):
    message = request.form.get('message')
    if not message:
        flash("DM message cannot be empty.")
        return redirect(url_for('dm_templates', bot_id=bot_id))
    tid = uuid.uuid4().hex
    execute("INSERT INTO dm_templates (id, bot_id, message) VALUES (?, ?, ?)", (tid, bot_id, message))
    flash("DM template added.")
    return redirect(url_for('dm_templates', bot_id=bot_id))

@app.route('/bot/<bot_id>/dm_templates/delete/<dm_id>', methods=['POST'])
def delete_dm_template(bot_id, dm_id):
    execute("DELETE FROM dm_templates WHERE id=? AND bot_id=?", (dm_id, bot_id))
    flash("DM template deleted.")
    return redirect(url_for('dm_templates', bot_id=bot_id))

@app.route('/bot/<bot_id>/logs')
def bot_logs(bot_id):
    bot = query_one("SELECT * FROM bots WHERE id=?", (bot_id,))
    if not bot:
        flash("Bot not found.")
        return redirect(url_for('index'))

    logs = query_all("""
        SELECT activity.*, accounts.username FROM activity
        LEFT JOIN accounts ON activity.account_id = accounts.id
        WHERE activity.bot_id=?
        ORDER BY ts DESC LIMIT 100
    """, (bot_id,))
    return render_template('logs.html', bot=bot, logs=logs)




if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

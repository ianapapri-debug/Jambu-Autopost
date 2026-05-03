from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import hashlib
import os
import threading
import time
import requests
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.urandom(24)

DB_PATH = 'instance/jambu.db'

# ─── Scheduler Storage ───────────────────────────────────────────────
active_schedulers = {}  # channel_id -> threading.Event

# ─── Database ────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs('instance', exist_ok=True)
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        token TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL,
        interval INTEGER NOT NULL DEFAULT 60,
        message TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        last_sent TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        webhook_url TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # Default admin
    admin_pass = hash_password('admin123')
    c.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
              ('admin', admin_pass, 'admin'))

    conn.commit()
    conn.close()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ─── Decorators ───────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Akses ditolak. Hanya admin yang bisa mengakses halaman ini.', 'error')
            return redirect(url_for('channels'))
        return f(*args, **kwargs)
    return decorated

# ─── Logging Helper ───────────────────────────────────────────────────
def log_action(user_id, username, action, detail=''):
    conn = get_db()
    conn.execute("INSERT INTO logs (user_id, username, action, detail) VALUES (?, ?, ?, ?)",
                 (user_id, username, action, detail))
    conn.commit()
    conn.close()

# ─── Discord Autopost Scheduler ───────────────────────────────────────
def send_discord_message(channel_id, token, message, webhook_url=None, token_name=''):
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }
    payload = {"content": message}
    status = "SUCCESS"
    status_code = 0
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        status_code = resp.status_code
        if resp.status_code not in [200, 201]:
            status = "FAILED"
    except Exception as e:
        status = "FAILED"

    # Send to webhook if configured
    if webhook_url:
        try:
            now = datetime.now()
            is_success = status == "SUCCESS"
            color = 0x57F287 if is_success else 0xED4245
            status_icon = "✅" if is_success else "❌"
            preview = message[:80] + "..." if len(message) > 80 else message

            embed = {
                "embeds": [{
                    "title": f"{status_icon}  Autopost {'Berhasil' if is_success else 'Gagal'}",
                    "description": f"```\n{preview}\n```",
                    "color": color,
                    "fields": [
                        {
                            "name": "🤖  Token",
                            "value": f"`{token_name or 'Unknown'}`",
                            "inline": True
                        },
                        {
                            "name": "📡  Channel ID",
                            "value": f"`{channel_id}`",
                            "inline": True
                        },
                        {
                            "name": "📶  HTTP Status",
                            "value": f"`{status_code}`",
                            "inline": True
                        },
                        {
                            "name": "🗓️  Tanggal",
                            "value": now.strftime('%d %b %Y'),
                            "inline": True
                        },
                        {
                            "name": "🕐  Waktu",
                            "value": now.strftime('%H:%M:%S WIB'),
                            "inline": True
                        },
                        {
                            "name": "📊  Status",
                            "value": f"{'`SUCCESS`' if is_success else '`FAILED`'}",
                            "inline": True
                        },
                    ],
                    "footer": {
                        "text": "Jambu Store Autopost • Bot Notifikasi"
                    },
                    "timestamp": now.isoformat()
                }]
            }
            requests.post(webhook_url, json=embed, timeout=5)
        except Exception:
            pass

    return status

def autopost_worker(channel_row_id, stop_event):
    while not stop_event.is_set():
        conn = get_db()
        row = conn.execute(
            "SELECT c.*, t.token, t.name as token_name, s.webhook_url "
            "FROM channels c "
            "LEFT JOIN tokens t ON t.user_id = c.user_id "
            "LEFT JOIN settings s ON s.user_id = c.user_id "
            "WHERE c.id = ? AND c.is_active = 1 ORDER BY t.id LIMIT 1",
            (channel_row_id,)
        ).fetchone()

        if not row:
            conn.close()
            break

        if row['token']:
            send_discord_message(
                row['channel_id'],
                row['token'],
                row['message'],
                row['webhook_url'],
                row['token_name']
            )

        conn.execute("UPDATE channels SET last_sent = datetime('now','localtime') WHERE id = ?",
                     (channel_row_id,))
        conn.commit()
        conn.close()

        stop_event.wait(row['interval'])

def start_autopost(channel_row_id):
    if channel_row_id in active_schedulers:
        active_schedulers[channel_row_id].set()

    stop_event = threading.Event()
    active_schedulers[channel_row_id] = stop_event
    t = threading.Thread(target=autopost_worker, args=(channel_row_id, stop_event), daemon=True)
    t.start()

def stop_autopost(channel_row_id):
    if channel_row_id in active_schedulers:
        active_schedulers[channel_row_id].set()
        del active_schedulers[channel_row_id]

# ─── Auth Routes ──────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard') if session['role'] == 'admin' else url_for('channels'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ? AND password = ?",
                            (username, hash_password(password))).fetchone()
        conn.close()

        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            log_action(user['id'], user['username'], 'Login', f"Login berhasil dari IP {request.remote_addr}")
            flash(f'Selamat datang, {user["username"]}!', 'success')
            return redirect(url_for('dashboard') if user['role'] == 'admin' else url_for('channels'))
        else:
            flash('Username atau password salah.', 'error')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_action(session['user_id'], session['username'], 'Logout', 'User logout')
    session.clear()
    flash('Berhasil logout.', 'success')
    return redirect(url_for('login'))

# ─── Dashboard ────────────────────────────────────────────────────────
@app.route('/dashboard')
@admin_required
def dashboard():
    conn = get_db()
    total_channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    total_tokens = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_channels = conn.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1").fetchone()[0]
    recent_logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    return render_template('dashboard.html',
                           total_channels=total_channels,
                           total_tokens=total_tokens,
                           total_users=total_users,
                           active_channels=active_channels,
                           recent_logs=recent_logs)

# ─── Channels ─────────────────────────────────────────────────────────
@app.route('/channels')
@login_required
def channels():
    conn = get_db()
    if session['role'] == 'admin':
        rows = conn.execute(
            "SELECT c.*, u.username FROM channels c JOIN users u ON c.user_id = u.id ORDER BY c.id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, u.username FROM channels c JOIN users u ON c.user_id = u.id WHERE c.user_id = ? ORDER BY c.id DESC",
            (session['user_id'],)
        ).fetchall()
    conn.close()
    return render_template('channels.html', channels=rows)

@app.route('/channels/add', methods=['POST'])
@login_required
def add_channel():
    mode = request.form.get('mode', 'single')

    # Ambil interval sesuai mode
    if mode == 'bulk':
        interval_raw = request.form.get('interval_bulk', '60').strip()
        raw_ids = request.form.get('bulk_area', '').strip()
    else:
        interval_raw = request.form.get('interval_single', '60').strip()
        raw_ids = request.form.get('single_id', '').strip()

    try:
        interval = int(interval_raw)
    except ValueError:
        interval = 60

    message = request.form.get('message', '').strip()

    if interval < 60:
        flash('Interval minimal 60 detik.', 'error')
        return redirect(url_for('channels'))

    if not message:
        flash('Pesan tidak boleh kosong.', 'error')
        return redirect(url_for('channels'))

    if not raw_ids:
        flash('Channel ID tidak boleh kosong.', 'error')
        return redirect(url_for('channels'))

    channel_ids = [x.strip() for x in raw_ids.splitlines() if x.strip()]
    if len(channel_ids) > 100:
        channel_ids = channel_ids[:100]
        flash('Maksimal 100 channel. Hanya 100 pertama yang ditambahkan.', 'warning')

    conn = get_db()
    added = 0
    for cid in channel_ids:
        if cid.isdigit():
            conn.execute(
                "INSERT INTO channels (user_id, channel_id, interval, message) VALUES (?, ?, ?, ?)",
                (session['user_id'], cid, interval, message)
            )
            added += 1

    conn.commit()
    conn.close()

    log_action(session['user_id'], session['username'], 'Add Channel',
               f"Menambahkan {added} channel")
    flash(f'Berhasil menambahkan {added} channel.', 'success')
    return redirect(url_for('channels'))

@app.route('/channels/edit/<int:cid>', methods=['POST'])
@login_required
def edit_channel(cid):
    interval = int(request.form.get('interval', 60))
    message = request.form.get('message', '').strip()

    if interval < 60:
        flash('Interval minimal 60 detik.', 'error')
        return redirect(url_for('channels'))

    conn = get_db()
    if session['role'] == 'admin':
        conn.execute("UPDATE channels SET interval = ?, message = ? WHERE id = ?",
                     (interval, message, cid))
    else:
        conn.execute("UPDATE channels SET interval = ?, message = ? WHERE id = ? AND user_id = ?",
                     (interval, message, cid, session['user_id']))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Edit Channel', f"Edit channel ID {cid}")
    flash('Channel berhasil diperbarui.', 'success')
    return redirect(url_for('channels'))

@app.route('/channels/delete/<int:cid>', methods=['POST'])
@login_required
def delete_channel(cid):
    stop_autopost(cid)
    conn = get_db()
    if session['role'] == 'admin':
        conn.execute("DELETE FROM channels WHERE id = ?", (cid,))
    else:
        conn.execute("DELETE FROM channels WHERE id = ? AND user_id = ?", (cid, session['user_id']))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Delete Channel', f"Hapus channel ID {cid}")
    flash('Channel berhasil dihapus.', 'success')
    return redirect(url_for('channels'))

@app.route('/channels/toggle/<int:cid>', methods=['POST'])
@login_required
def toggle_channel(cid):
    conn = get_db()
    if session['role'] == 'admin':
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (cid,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM channels WHERE id = ? AND user_id = ?",
                           (cid, session['user_id'])).fetchone()

    if not row:
        conn.close()
        flash('Channel tidak ditemukan.', 'error')
        return redirect(url_for('channels'))

    new_status = 0 if row['is_active'] else 1
    conn.execute("UPDATE channels SET is_active = ? WHERE id = ?", (new_status, cid))
    conn.commit()
    conn.close()

    if new_status == 1:
        start_autopost(cid)
        log_action(session['user_id'], session['username'], 'Start Autopost', f"Start channel {row['channel_id']}")
        flash(f'Autopost channel {row["channel_id"]} dimulai.', 'success')
    else:
        stop_autopost(cid)
        log_action(session['user_id'], session['username'], 'Stop Autopost', f"Stop channel {row['channel_id']}")
        flash(f'Autopost channel {row["channel_id"]} dihentikan.', 'success')

    return redirect(url_for('channels'))

# ─── Start All / Stop All ─────────────────────────────────────────────
@app.route('/channels/start-all', methods=['POST'])
@login_required
def start_all_channels():
    conn = get_db()
    if session['role'] == 'admin':
        rows = conn.execute("SELECT * FROM channels WHERE is_active = 0").fetchall()
        conn.execute("UPDATE channels SET is_active = 1 WHERE is_active = 0")
    else:
        rows = conn.execute("SELECT * FROM channels WHERE is_active = 0 AND user_id = ?",
                            (session['user_id'],)).fetchall()
        conn.execute("UPDATE channels SET is_active = 1 WHERE is_active = 0 AND user_id = ?",
                     (session['user_id'],))
    conn.commit()
    conn.close()

    count = 0
    for row in rows:
        start_autopost(row['id'])
        count += 1

    log_action(session['user_id'], session['username'], 'Start All', f"Start {count} channel sekaligus")
    flash(f'{count} channel berhasil dijalankan.', 'success')
    return redirect(url_for('channels'))

@app.route('/channels/stop-all', methods=['POST'])
@login_required
def stop_all_channels():
    conn = get_db()
    if session['role'] == 'admin':
        rows = conn.execute("SELECT * FROM channels WHERE is_active = 1").fetchall()
        conn.execute("UPDATE channels SET is_active = 0 WHERE is_active = 1")
    else:
        rows = conn.execute("SELECT * FROM channels WHERE is_active = 1 AND user_id = ?",
                            (session['user_id'],)).fetchall()
        conn.execute("UPDATE channels SET is_active = 0 WHERE is_active = 1 AND user_id = ?",
                     (session['user_id'],))
    conn.commit()
    conn.close()

    count = 0
    for row in rows:
        stop_autopost(row['id'])
        count += 1

    log_action(session['user_id'], session['username'], 'Stop All', f"Stop {count} channel sekaligus")
    flash(f'{count} channel berhasil dihentikan.', 'success')
    return redirect(url_for('channels'))

# ─── Settings ─────────────────────────────────────────────────────────
@app.route('/settings')
@login_required
def settings():
    conn = get_db()
    tokens = conn.execute("SELECT * FROM tokens WHERE user_id = ? ORDER BY id DESC",
                          (session['user_id'],)).fetchall()
    setting = conn.execute("SELECT * FROM settings WHERE user_id = ?",
                           (session['user_id'],)).fetchone()
    conn.close()
    return render_template('settings.html', tokens=tokens, setting=setting)

@app.route('/settings/token/add', methods=['POST'])
@login_required
def add_token():
    name = request.form.get('name', '').strip()
    token = request.form.get('token', '').strip()
    if not name or not token:
        flash('Nama dan token tidak boleh kosong.', 'error')
        return redirect(url_for('settings'))
    conn = get_db()
    conn.execute("INSERT INTO tokens (user_id, name, token) VALUES (?, ?, ?)",
                 (session['user_id'], name, token))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Add Token', f"Token '{name}' ditambahkan")
    flash(f'Token "{name}" berhasil ditambahkan.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/token/delete/<int:tid>', methods=['POST'])
@login_required
def delete_token(tid):
    conn = get_db()
    conn.execute("DELETE FROM tokens WHERE id = ? AND user_id = ?", (tid, session['user_id']))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Delete Token', f"Token ID {tid} dihapus")
    flash('Token berhasil dihapus.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/webhook', methods=['POST'])
@login_required
def save_webhook():
    webhook = request.form.get('webhook', '').strip()
    conn = get_db()
    conn.execute("INSERT INTO settings (user_id, webhook_url) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET webhook_url = ?",
                 (session['user_id'], webhook, webhook))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Update Webhook', 'Webhook URL diperbarui')
    flash('Webhook berhasil disimpan.', 'success')
    return redirect(url_for('settings'))

# ─── Activity Log ─────────────────────────────────────────────────────
@app.route('/logs')
@admin_required
def activity_logs():
    conn = get_db()
    logs = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return render_template('logs.html', logs=logs)

# ─── User Management ─────────────────────────────────────────────────
@app.route('/users')
@admin_required
def user_management():
    conn = get_db()
    users = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return render_template('users.html', users=users)

@app.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', 'user')

    if not username or not password:
        flash('Username dan password tidak boleh kosong.', 'error')
        return redirect(url_for('user_management'))

    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                     (username, hash_password(password), role))
        conn.commit()
        log_action(session['user_id'], session['username'], 'Add User',
                   f"User '{username}' ({role}) ditambahkan")
        flash(f'User "{username}" berhasil dibuat.', 'success')
    except sqlite3.IntegrityError:
        flash('Username sudah digunakan.', 'error')
    finally:
        conn.close()
    return redirect(url_for('user_management'))

@app.route('/users/delete/<int:uid>', methods=['POST'])
@admin_required
def delete_user(uid):
    if uid == session['user_id']:
        flash('Tidak bisa menghapus akun sendiri.', 'error')
        return redirect(url_for('user_management'))
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Delete User', f"User ID {uid} dihapus")
    flash('User berhasil dihapus.', 'success')
    return redirect(url_for('user_management'))

@app.route('/users/edit/<int:uid>', methods=['POST'])
@admin_required
def edit_user(uid):
    role = request.form.get('role', 'user')
    new_password = request.form.get('password', '').strip()
    conn = get_db()
    if new_password:
        conn.execute("UPDATE users SET role = ?, password = ? WHERE id = ?",
                     (role, hash_password(new_password), uid))
    else:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
    conn.commit()
    conn.close()
    log_action(session['user_id'], session['username'], 'Edit User', f"User ID {uid} diperbarui")
    flash('User berhasil diperbarui.', 'success')
    return redirect(url_for('user_management'))

# ─── API: Get channel data for edit modal ─────────────────────────────
@app.route('/api/channel/<int:cid>')
@login_required
def api_channel(cid):
    conn = get_db()
    row = conn.execute("SELECT * FROM channels WHERE id = ?", (cid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'id': row['id'],
        'channel_id': row['channel_id'],
        'interval': row['interval'],
        'message': row['message']
    })

if __name__ == '__main__':
    init_db()
    print("=" * 50)
    print("  Jambu Store Autopost - Starting Server")
    print("  URL: http://localhost:5000")
    print("  Default: admin / admin123")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)

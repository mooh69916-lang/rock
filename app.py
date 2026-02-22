from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify, make_response
import io
import csv
import json
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError
import uuid
from datetime import datetime
import currency
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
EXPORT_FOLDER = os.path.join(BASE_DIR, 'static', 'exports')
os.makedirs(EXPORT_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET', 'change_this_secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25MB global max
app.config['MAX_VIDEO_FILE_SIZE'] = 20 * 1024 * 1024  # 20MB per-video limit


DB_PATH = os.path.join(BASE_DIR, 'app.db')

def get_db():
    # Use a short timeout and allow cross-thread access (Flask dev server may use threads).
    # Also enable WAL mode for better concurrent read/write behavior.
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute('PRAGMA journal_mode = WAL')
        cur.execute('PRAGMA foreign_keys = ON')
    except Exception:
        # If pragmas fail, continue with the connection — they are advisory
        pass
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(open(os.path.join(BASE_DIR, 'schema.sql')).read())
    conn.commit()
    conn.close()

# Ensure the database schema exists when the app starts under WSGI
try:
    if not os.path.exists(DB_PATH):
        init_db()
except Exception:
    # Avoid crashing on import; errors will surface in logs
    import sys
    print('Warning: failed to initialize database schema', file=sys.stderr)

# Ensure investments table has optional columns used by newer codepaths
def ensure_investment_columns():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(investments)")
        cols = [r[1] for r in cur.fetchall()]
        needed = []
        if 'amount_usd' not in cols:
            needed.append("ALTER TABLE investments ADD COLUMN amount_usd REAL DEFAULT 0.0")
        if 'amount_local' not in cols:
            needed.append("ALTER TABLE investments ADD COLUMN amount_local REAL")
        if 'currency_code' not in cols:
            needed.append("ALTER TABLE investments ADD COLUMN currency_code TEXT")
        if 'current_profit' not in cols:
            needed.append("ALTER TABLE investments ADD COLUMN current_profit REAL DEFAULT 0.0")
        for s in needed:
            try:
                cur.execute(s)
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

try:
    ensure_investment_columns()
except Exception:
    pass

@app.route('/')
def index():
    # server-side pagination
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    PER_PAGE = 9
    offset = (page - 1) * PER_PAGE
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM investment_plans WHERE status = 'active'")
    count_row = cur.fetchone()
    total = count_row['cnt'] if count_row and 'cnt' in count_row.keys() else 0
    # if logged in, fetch user to provide currency symbol in templates
    user = None
    if 'user_id' in session:
        try:
            cur.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
            user = cur.fetchone()
        except Exception:
            user = None
    # normalize sqlite3.Row to dict for template compatibility
    if user:
        try:
            user = dict(user)
        except Exception:
            pass
    cur.execute("SELECT * FROM investment_plans WHERE status = 'active' ORDER BY id DESC LIMIT ? OFFSET ?", (PER_PAGE, offset))
    raw_plans = cur.fetchall()
    # determine user's currency code and symbol
    user_currency = None
    user_symbol = None
    if user:
        try:
            user_currency = user['currency_code']
            user_symbol = user['currency_symbol']
        except Exception:
            user_currency = session.get('currency_code')
            user_symbol = session.get('currency_symbol')
    else:
        user_currency = session.get('currency_code')
        user_symbol = session.get('currency_symbol')

    # prepare display plans with conversion (plans stored in USD)
    plans = []
    for p in raw_plans:
        try:
            amount_usd = float(p['minimum_amount'] or 0)
        except Exception:
            amount_usd = 0.0
        try:
            profit_usd = float(p['total_return'] or p['profit_amount'] or 0)
        except Exception:
            profit_usd = 0.0
        display_amount = currency.convert_usd_to(user_currency, amount_usd) if user_currency else None
        display_profit = currency.convert_usd_to(user_currency, profit_usd) if user_currency else None
        # build a simple dict-like object for templates
        plans.append({
            'id': p['id'],
            'name': p['plan_name'],
            'duration': p['duration_days'],
            'amount': amount_usd,
            'profit': profit_usd,
            'display_amount': display_amount,
            'display_profit': display_profit,
            'currency_symbol': user_symbol or '₦',
            'capital_back': p['capital_back'] if 'capital_back' in p.keys() else 1,
            'funded_pct': float(p['funded_pct']) if 'funded_pct' in p.keys() and p['funded_pct'] is not None else 0.0,
            'investors': int(p['investors']) if 'investors' in p.keys() and p['investors'] is not None else 0,
            'views': int(p['views']) if 'views' in p.keys() and p['views'] is not None else 0
        })
    # fetch active announcements for homepage
    now = datetime.utcnow().isoformat()
    try:
        cur.execute("SELECT * FROM announcements WHERE is_active = 1 AND (start_date IS NULL OR start_date <= ?) AND (end_date IS NULL OR end_date >= ?) ORDER BY created_at DESC", (now, now))
        announcements = cur.fetchall()
    except Exception:
        announcements = []
    conn.close()
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template('index.html', plans=plans, page=page, total_pages=total_pages, announcements=announcements, user=user)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        country = request.form.get('country')
        currency_code = request.form.get('currency_code')
        currency_symbol = request.form.get('currency_symbol')
        currency_name = request.form.get('currency_name')
        pw_hash = generate_password_hash(password)
        conn = get_db()
        cur = conn.cursor()
        # determine whether this should be the first admin user
        try:
            cur.execute('SELECT COUNT(*) as cnt FROM users')
            cnt = cur.fetchone()['cnt']
        except Exception:
            cnt = 0
        is_admin_flag = 1 if cnt == 0 else 0
        try:
            cur.execute('INSERT INTO users (username, email, password_hash, balance, policy_accepted, is_admin, country, currency_code, currency_symbol, currency_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (username, email, pw_hash, 0.0, 0, is_admin_flag, country, currency_code, currency_symbol, currency_name, datetime.utcnow()))
            conn.commit()
            flash('Registered. Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.OperationalError as oe:
            # older DB without currency columns: fallback to insert without them
            if 'no column named country' in str(oe) or 'has no column' in str(oe):
                try:
                    cur.execute('INSERT INTO users (username, email, password_hash, balance, policy_accepted, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                (username, email, pw_hash, 0.0, 0, is_admin_flag, datetime.utcnow()))
                    conn.commit()
                    flash('Registered (legacy DB). Please login.', 'success')
                    return redirect(url_for('login'))
                except sqlite3.IntegrityError:
                    flash('Username or email already exists', 'danger')
                except Exception:
                    flash('Registration failed', 'danger')
                finally:
                    conn.close()
            else:
                conn.close()
                raise
        except sqlite3.IntegrityError:
            flash('Username or email already exists', 'danger')
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cur.fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['is_admin'] = user['is_admin']
            # store currency preferences in session for templates
            try:
                session['currency_code'] = user['currency_code']
                session['currency_symbol'] = user['currency_symbol']
                session['currency_name'] = user['currency_name']
            except Exception:
                # ignore if columns missing
                pass
            flash('Logged in', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin only', 'danger')
            return redirect(url_for('index'))
        return fn(*args, **kwargs)
    return wrapper

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    if user:
        try:
            user = dict(user)
        except Exception:
            pass

    # fetch plans
    cur.execute('SELECT * FROM investment_plans')
    raw_plans = cur.fetchall()

    # convert user balance to display currency
    display_balance = None
    try:
        user_currency = user['currency_code'] if user and 'currency_code' in user.keys() else session.get('currency_code')
        display_balance = currency.convert_usd_to(user_currency, user['balance']) if user_currency else None
    except Exception:
        display_balance = None

    # prepare plans list similar to index
    plans = []
    for p in raw_plans:
        amount_usd = float(p['minimum_amount'] or 0)
        profit_usd = float(p['total_return'] or p['profit_amount'] or 0)
        display_amount = currency.convert_usd_to(user_currency, amount_usd) if user_currency else None
        display_profit = currency.convert_usd_to(user_currency, profit_usd) if user_currency else None
        plans.append({
            'id': p['id'],
            'plan_name': p['plan_name'],
            'duration_days': p['duration_days'],
            'amount': amount_usd,
            'profit': profit_usd,
            'display_amount': display_amount,
            'display_profit': display_profit,
            'currency_symbol': (user['currency_symbol'] if user and 'currency_symbol' in user.keys() else session.get('currency_symbol')) or '₦'
        })

    # fetch user's investments to show on dashboard
    try:
        cur.execute('SELECT * FROM investments WHERE user_id = ? ORDER BY id DESC', (session['user_id'],))
        user_investments = cur.fetchall()
    except Exception:
        user_investments = []

    # compute aggregates for dashboard: active investments and current profit (USD)
    active_investments_total = 0.0
    current_profit_total = 0.0
    # build plan minimum map for fallback when investments lack amount_usd column
    plan_min_map = {}
    try:
        for p in raw_plans:
            try:
                plan_min_map[p['id']] = float(p['minimum_amount'] or 0)
            except Exception:
                plan_min_map[getattr(p, 'id', None)] = 0.0
    except Exception:
        plan_min_map = {}
    try:
        for inv in user_investments:
            status = inv['status'] if 'status' in inv.keys() else None
            if status and str(status).lower() == 'active':
                if 'amount_usd' in inv.keys():
                    try:
                        amt = float(inv['amount_usd'] or 0)
                    except Exception:
                        amt = 0.0
                else:
                    amt = plan_min_map.get(inv['plan_id'], 0.0)
                if 'current_profit' in inv.keys():
                    try:
                        prof = float(inv['current_profit'] or 0)
                    except Exception:
                        prof = 0.0
                else:
                    prof = 0.0
                active_investments_total += amt
                current_profit_total += prof
    except Exception:
        pass

    conn.close()
    return render_template('dashboard.html', user=user, plans=plans, display_balance=display_balance, user_investments=user_investments, active_investments=active_investments_total, current_profit=current_profit_total)


@app.route('/plans/<int:plan_id>')
def plan_detail(plan_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM investment_plans WHERE id = ?', (plan_id,))
    plan = cur.fetchone()
    if not plan:
        conn.close()
        return ('Plan not found', 404)
    # increment view count in plan_stats (create or update)
    try:
        cur.execute('SELECT total_views, total_investors FROM plan_stats WHERE plan_id = ?', (plan_id,))
        stats = cur.fetchone()
        if stats:
            cur.execute('UPDATE plan_stats SET total_views = total_views + 1 WHERE plan_id = ?', (plan_id,))
        else:
            cur.execute('INSERT INTO plan_stats (plan_id, total_views, total_investors) VALUES (?, ?, ?)', (plan_id, 1, 0))
        conn.commit()
        cur.execute('SELECT total_views, total_investors FROM plan_stats WHERE plan_id = ?', (plan_id,))
        stats = cur.fetchone()
    except Exception:
        stats = {'total_views': 0, 'total_investors': 0}
    # convert amounts for display if user logged in
    display_amount = None
    display_profit = None
    currency_symbol = '₦'
    if 'user_id' in session:
        try:
            cur.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
            user = cur.fetchone()
            user_currency = user['currency_code'] if 'currency_code' in user.keys() else session.get('currency_code')
            currency_symbol = user['currency_symbol'] if 'currency_symbol' in user.keys() else session.get('currency_symbol') or '₦'
            amount_usd = float(plan['minimum_amount'] or 0)
            profit_usd = float(plan['total_return'] or plan['profit_amount'] or 0)
            display_amount = currency.convert_usd_to(user_currency, amount_usd)
            display_profit = currency.convert_usd_to(user_currency, profit_usd)
            try:
                cur.execute('SELECT min_amount, max_amount FROM investment_settings LIMIT 1')
                s = cur.fetchone()
                if s and 'min_amount' in s.keys():
                    min_usd = max(float(s['min_amount']), 10.0, amount_usd)
                    max_usd = float(s['max_amount'])
                else:
                    min_usd = max(amount_usd, 10.0)
                    max_usd = None
            except Exception:
                min_usd = max(amount_usd, 10.0)
                max_usd = None
            display_min_local = currency.convert_usd_to(user_currency, min_usd)
            display_max_local = currency.convert_usd_to(user_currency, max_usd) if max_usd is not None else None
            rate_for_display = currency.get_rate(user_currency)
        except Exception:
            pass
    conn.close()
    plan_dict = dict(plan)
    plan_dict['display_amount'] = display_amount
    plan_dict['display_profit'] = display_profit
    plan_dict['currency_symbol'] = currency_symbol
    try:
        plan_dict['min_usd'] = min_usd
        plan_dict['display_min_local'] = display_min_local
        plan_dict['max_usd'] = max_usd
        plan_dict['display_max_local'] = display_max_local
        plan_dict['rate'] = rate_for_display
    except Exception:
        plan_dict['min_usd'] = 10.0
        plan_dict['display_min_local'] = None
        plan_dict['max_usd'] = None
        plan_dict['display_max_local'] = None
        plan_dict['rate'] = None
    return render_template('plan_detail.html', plan=plan_dict, stats=stats)

@app.route('/policy')
@login_required
def policy():
    return render_template('policy.html')

@app.route('/accept_policy', methods=['POST'])
@login_required
def accept_policy():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE users SET policy_accepted = 1 WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    flash('Policy accepted', 'success')
    return redirect(url_for('dashboard'))

@app.route('/invest', methods=['POST'])
@login_required
def invest():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT policy_accepted FROM users WHERE id = ?', (session['user_id'],))
    row = cur.fetchone()
    if not row or row['policy_accepted'] != 1:
        flash('You must accept the investment policy before investing.', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    plan_id = request.form.get('plan_id')
    # accept optional local amount (user-entered) else use plan minimum (USD)
    local_amount = request.form.get('local_amount')
    conn2 = get_db()
    cur2 = conn2.cursor()
    cur2.execute('SELECT * FROM investment_plans WHERE id = ?', (plan_id,))
    plan = cur2.fetchone()
    # determine user's currency code
    try:
        cur2.execute('SELECT currency_code FROM users WHERE id = ?', (session['user_id'],))
        urow = cur2.fetchone()
        user_currency = urow['currency_code'] if urow and 'currency_code' in urow.keys() else session.get('currency_code')
    except Exception:
        user_currency = session.get('currency_code')
    # compute amounts
    try:
        if local_amount:
            amount_local = float(local_amount)
            # convert local to USD for internal accounting
            amount_usd = currency.convert_to_usd(user_currency, amount_local) or float(plan['minimum_amount'])
        else:
            # use plan minimum USD
            amount_usd = float(plan['minimum_amount']) if plan else 0.0
            amount_local = currency.convert_usd_to(user_currency, amount_usd) if user_currency else None
    except Exception:
        amount_local = None
        amount_usd = float(plan['minimum_amount']) if plan else 0.0
    finally:
        try:
            conn2.close()
        except Exception:
            pass
    # create investment pending (no automatic credit)
    # try to insert with new currency columns; fallback if older schema
    try:
        cur.execute('INSERT INTO investments (user_id, plan_id, status, proof_image, amount_usd, amount_local, currency_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (session['user_id'], plan_id, 'pending', '', amount_usd, amount_local, user_currency, datetime.utcnow()))
    except sqlite3.OperationalError:
        cur.execute('INSERT INTO investments (user_id, plan_id, status, proof_image, created_at) VALUES (?, ?, ?, ?, ?)',
                    (session['user_id'], plan_id, 'pending', '', datetime.utcnow()))
    conn.commit()
    conn.close()
    flash('Investment request created. Upload payment proof.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/upload_proof/<int:investment_id>', methods=['POST'])
@login_required
def upload_proof(investment_id):
    if 'proof' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('dashboard'))
    file = request.files['proof']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('dashboard'))
    filename = secure_filename(file.filename)
    ALLOWED_EXT = ('.png', '.jpg', '.jpeg', '.gif')
    if not filename.lower().endswith(ALLOWED_EXT):
        flash('Invalid file extension', 'danger')
        return redirect(url_for('dashboard'))
    # create a unique filename to avoid collisions
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    # validate image content using Pillow
    try:
        img = Image.open(file.stream)
        img.verify()
    except (UnidentifiedImageError, Exception):
        flash('Uploaded file is not a valid image', 'danger')
        return redirect(url_for('dashboard'))
    # reset stream and save file
    file.stream.seek(0)
    file.save(save_path)
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE investments SET proof_image = ? WHERE id = ? AND user_id = ?',
                (unique_name, investment_id, session['user_id']))
    conn.commit()
    conn.close()
    flash('Proof uploaded', 'success')
    return redirect(url_for('dashboard'))

@app.route('/withdraw', methods=['POST'])
@login_required
def withdraw():
    try:
        amount = float(request.form.get('amount', 0))
    except ValueError:
        flash('Invalid amount', 'danger')
        return redirect(url_for('dashboard'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT balance FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    if not user or amount <= 0:
        flash('Invalid amount', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    cur.execute('SELECT min_amount, max_amount FROM withdrawal_settings LIMIT 1')
    settings = cur.fetchone()
    if settings:
        if amount < settings['min_amount'] or amount > settings['max_amount']:
            flash('Amount outside allowed withdrawal limits', 'danger')
            conn.close()
            return redirect(url_for('dashboard'))
    if amount > user['balance']:
        flash('Insufficient balance', 'danger')
        conn.close()
        return redirect(url_for('dashboard'))
    cur.execute('INSERT INTO withdrawals (user_id, amount, status, requested_at) VALUES (?, ?, ?, ?)',
                (session['user_id'], amount, 'pending', datetime.utcnow()))
    conn.commit()
    conn.close()
    flash('Withdrawal request created', 'info')
    return redirect(url_for('dashboard'))

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users')
    users = cur.fetchall()
    cur.execute('SELECT * FROM investments WHERE status = ?', ('pending',))
    investments = cur.fetchall()
    cur.execute('SELECT * FROM withdrawals WHERE status = ?', ('pending',))
    withdrawals = cur.fetchall()
    cur.execute('SELECT * FROM withdrawal_settings LIMIT 1')
    settings = cur.fetchone()
    conn.close()
    return render_template('admin/dashboard.html', users=users, investments=investments, withdrawals=withdrawals, settings=settings)


@app.route('/admin/contact', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        whatsapp = request.form.get('whatsapp', '').strip()
        data = {'name': name, 'phone': phone, 'whatsapp': whatsapp}
        ok = write_admin_contact(data)
        if ok:
            flash('Admin contact updated', 'success')
        else:
            flash('Failed to save admin contact', 'danger')
        return redirect(url_for('admin_contact'))

    contact = read_admin_contact()
    return render_template('admin/admin_contact.html', contact=contact)


@app.route('/admin/exchange_rates')
@login_required
@admin_required
def admin_exchange_rates():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT currency_code, rate, updated_at FROM exchange_rates ORDER BY currency_code')
        rates = cur.fetchall()
    except Exception:
        rates = []
    conn.close()
    return render_template('admin/exchange_rates.html', rates=rates)


@app.route('/admin/exchange_rates/<string:code>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_exchange_rate_edit(code):
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            rate = float(request.form.get('rate'))
            cur.execute('INSERT OR REPLACE INTO exchange_rates (currency_code, rate, updated_at) VALUES (?, ?, ?)', (code.upper(), rate, datetime.utcnow().isoformat()))
            conn.commit()
            flash('Rate updated', 'success')
            return redirect(url_for('admin_exchange_rates'))
        except Exception:
            flash('Failed to update rate', 'danger')
    try:
        cur.execute('SELECT currency_code, rate, updated_at FROM exchange_rates WHERE currency_code = ?', (code.upper(),))
        r = cur.fetchone()
    except Exception:
        r = None
    conn.close()
    if not r:
        r = {'currency_code': code.upper(), 'rate': ''}
    return render_template('admin/exchange_rate_form.html', rate=r)


@app.route('/admin/exchange_rates/update', methods=['POST'])
@login_required
@admin_required
def admin_exchange_rates_update():
    # trigger the scripts/update_exchange_rates.py script to refresh rates
    import subprocess, sys
    script = os.path.join(BASE_DIR, 'scripts', 'update_exchange_rates.py')
    try:
        subprocess.check_call([sys.executable, script])
        flash('Exchange rates updated (script ran)', 'success')
    except Exception as e:
        flash(f'Failed to update rates: {e}', 'danger')
    return redirect(url_for('admin_exchange_rates'))


@app.route('/admin/investment_settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_investment_settings():
    conn = get_db()
    cur = conn.cursor()
    if request.method == 'POST':
        try:
            min_amount = float(request.form.get('min_amount'))
            max_amount = float(request.form.get('max_amount'))
            cur.execute('SELECT id FROM investment_settings LIMIT 1')
            existing = cur.fetchone()
            if existing:
                cur.execute('UPDATE investment_settings SET min_amount = ?, max_amount = ?, updated_at = ? WHERE id = ?', (min_amount, max_amount, datetime.utcnow().isoformat(), existing['id']))
            else:
                cur.execute('INSERT INTO investment_settings (min_amount, max_amount, updated_at) VALUES (?, ?, ?)', (min_amount, max_amount, datetime.utcnow().isoformat()))
            conn.commit()
            flash('Investment settings updated', 'success')
            return redirect(url_for('admin_investment_settings'))
        except Exception:
            conn.rollback()
            flash('Failed to update settings', 'danger')
    try:
        cur.execute('SELECT * FROM investment_settings LIMIT 1')
        s = cur.fetchone()
    except Exception:
        s = None
    conn.close()
    return render_template('admin/investment_settings.html', settings=s)


@app.route('/admin/plans')
@login_required
@admin_required
def admin_plans():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM investment_plans ORDER BY id DESC')
    plans = cur.fetchall()
    conn.close()
    return render_template('admin/plans.html', plans=plans)


@app.route('/admin/plans/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_plans_new():
    if request.method == 'POST':
        name = request.form.get('plan_name')
        minimum = float(request.form.get('minimum_amount') or 0)
        profit = float(request.form.get('profit_amount') or 0)
        total = float(request.form.get('total_return') or (minimum + profit))
        duration = int(request.form.get('duration_days') or 1)
        capital_back = 1 if request.form.get('capital_back') == 'on' else 0
        status = request.form.get('status') or 'inactive'
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO investment_plans (plan_name, minimum_amount, profit_amount, total_return, duration_days, capital_back, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (name, minimum, profit, total, duration, capital_back, status, datetime.utcnow(), datetime.utcnow()))
        conn.commit()
        conn.close()
        flash('Plan created', 'success')
        return redirect(url_for('admin_plans'))
    return render_template('admin/plan_form.html', plan=None)


@app.route('/admin/plans/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_plans_edit(plan_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM investment_plans WHERE id = ?', (plan_id,))
    plan = cur.fetchone()
    if not plan:
        conn.close()
        flash('Plan not found', 'danger')
        return redirect(url_for('admin_plans'))
    if request.method == 'POST':
        name = request.form.get('plan_name')
        minimum = float(request.form.get('minimum_amount') or 0)
        profit = float(request.form.get('profit_amount') or 0)
        total = float(request.form.get('total_return') or (minimum + profit))
        duration = int(request.form.get('duration_days') or 1)
        capital_back = 1 if request.form.get('capital_back') == 'on' else 0
        status = request.form.get('status') or 'inactive'
        cur.execute('UPDATE investment_plans SET plan_name = ?, minimum_amount = ?, profit_amount = ?, total_return = ?, duration_days = ?, capital_back = ?, status = ?, updated_at = ? WHERE id = ?',
                    (name, minimum, profit, total, duration, capital_back, status, datetime.utcnow(), plan_id))
        conn.commit()
        conn.close()
        flash('Plan updated', 'success')
        return redirect(url_for('admin_plans'))
    conn.close()
    return render_template('admin/plan_form.html', plan=plan)


@app.route('/admin/plans/<int:plan_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_plans_delete(plan_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        # count dependents
        cur.execute('SELECT COUNT(*) as cnt FROM investments WHERE plan_id = ?', (plan_id,))
        cnt_row = cur.fetchone()
        cnt = 0
        try:
            cnt = int(cnt_row['cnt']) if cnt_row and 'cnt' in cnt_row.keys() else int(cnt_row[0])
        except Exception:
            try:
                cnt = int(cnt_row[0]) if cnt_row else 0
            except Exception:
                cnt = 0

        # delete dependent investments first (destructive)
        if cnt > 0:
            try:
                cur.execute('DELETE FROM investments WHERE plan_id = ?', (plan_id,))
            except sqlite3.OperationalError:
                # table may not exist on older DBs; ignore
                pass

        # delete plan_stats and the plan
        try:
            cur.execute('DELETE FROM plan_stats WHERE plan_id = ?', (plan_id,))
        except sqlite3.OperationalError:
            # missing table is non-fatal
            pass
        try:
            cur.execute('DELETE FROM investment_plans WHERE id = ?', (plan_id,))
        except sqlite3.OperationalError:
            # unexpected, re-raise to be caught below
            raise
        conn.commit()
        conn.close()
        flash(f'Plan deleted. Removed {cnt} dependent investment(s).', 'info')
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        flash(f'Failed to delete plan: {e}', 'danger')
    return redirect(url_for('admin_plans'))


@app.route('/admin/plans/<int:plan_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_plans_toggle(plan_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT status FROM investment_plans WHERE id = ?', (plan_id,))
    p = cur.fetchone()
    if not p:
        conn.close()
        flash('Plan not found', 'danger')
        return redirect(url_for('admin_plans'))
    new_status = 'inactive' if p['status'] == 'active' else 'active'
    cur.execute('UPDATE investment_plans SET status = ? WHERE id = ?', (new_status, plan_id))
    conn.commit()
    conn.close()
    flash('Plan status updated', 'success')
    return redirect(url_for('admin_plans'))

@app.route('/admin/approve_investment/<int:inv_id>', methods=['POST'])
@login_required
@admin_required
def approve_investment(inv_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM investments WHERE id = ?', (inv_id,))
    inv = cur.fetchone()
    if not inv:
        flash('Investment not found', 'danger')
        conn.close()
        return redirect(url_for('admin_dashboard'))
    cur.execute('SELECT * FROM investment_plans WHERE id = ?', (inv['plan_id'],))
    plan = cur.fetchone()
    if plan:
        cur.execute('SELECT balance FROM users WHERE id = ?', (inv['user_id'],))
        user = cur.fetchone()
        if user:
            # plan stores amounts in USD (minimum_amount, profit_amount, total_return)
            profit_usd = plan.get('profit_amount') if isinstance(plan, dict) else plan['profit_amount']
            new_balance = user['balance'] + profit_usd
            cur.execute('UPDATE users SET balance = ? WHERE id = ?', (new_balance, inv['user_id']))
        # ensure investment row stores the amount and initializes current_profit when possible
        try:
            amount_val = None
            try:
                # if investment row already has amount_usd, leave it
                amount_val = inv['amount_usd'] if 'amount_usd' in inv.keys() else None
            except Exception:
                amount_val = None
            if amount_val is None:
                # fallback to plan minimum_amount
                try:
                    amt = float(plan['minimum_amount'] if isinstance(plan, dict) else plan['minimum_amount'])
                except Exception:
                    amt = 0.0
                # attempt to add columns if not present will raise OperationalError
                try:
                    cur.execute('UPDATE investments SET amount_usd = ?, current_profit = ? WHERE id = ?', (amt, 0.0, inv['id']))
                except sqlite3.OperationalError:
                    # older schema without those columns; ignore
                    pass
        except Exception:
            pass
    cur.execute('UPDATE investments SET status = ? WHERE id = ?', ('active', inv_id))
    # increment plan_stats.total_investors if plan_stats table exists
    try:
        cur.execute('SELECT total_investors FROM plan_stats WHERE plan_id = ?', (inv['plan_id'],))
        s = cur.fetchone()
        if s:
            cur.execute('UPDATE plan_stats SET total_investors = total_investors + 1 WHERE plan_id = ?', (inv['plan_id'],))
        else:
            cur.execute('INSERT INTO plan_stats (plan_id, total_views, total_investors) VALUES (?, ?, ?)', (inv['plan_id'], 0, 1))
    except Exception:
        pass
    conn.commit()
    conn.close()
    flash('Investment approved and balance updated', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject_investment/<int:inv_id>', methods=['POST'])
@login_required
@admin_required
def reject_investment(inv_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE investments SET status = ? WHERE id = ?', ('rejected', inv_id))
    conn.commit()
    conn.close()
    flash('Investment rejected', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/approve_withdrawal/<int:wid>', methods=['POST'])
@login_required
@admin_required
def approve_withdrawal(wid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM withdrawals WHERE id = ?', (wid,))
    w = cur.fetchone()
    if not w:
        flash('Withdrawal not found', 'danger')
        conn.close()
        return redirect(url_for('admin_dashboard'))
    cur.execute('SELECT balance FROM users WHERE id = ?', (w['user_id'],))
    user = cur.fetchone()
    if user and user['balance'] >= w['amount']:
        new_bal = user['balance'] - w['amount']
        cur.execute('UPDATE users SET balance = ? WHERE id = ?', (new_bal, w['user_id']))
        cur.execute('UPDATE withdrawals SET status = ? WHERE id = ?', ('approved', wid))
        conn.commit()
        flash('Withdrawal approved and balance deducted', 'success')
    else:
        flash('Insufficient balance to approve', 'danger')
    conn.close()
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/investments/<int:inv_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_investment_edit(inv_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM investments WHERE id = ?', (inv_id,))
    inv = cur.fetchone()
    if not inv:
        conn.close()
        flash('Investment not found', 'danger')
        return redirect(url_for('admin_dashboard'))
    # normalize row to dict-like if possible
    try:
        inv_dict = dict(inv)
    except Exception:
        inv_dict = inv

    if request.method == 'POST':
        # read new profit value
        new_profit_raw = request.form.get('current_profit')
        try:
            new_profit = float(new_profit_raw) if new_profit_raw not in (None, '') else None
        except ValueError:
            new_profit = None

        # ensure column exists, attempt update, otherwise add column and retry
        try:
            # fetch existing values for delta computation
            cur.execute('SELECT current_profit, status, user_id FROM investments WHERE id = ?', (inv_id,))
            old = cur.fetchone()
            old_profit = float(old['current_profit']) if old and old['current_profit'] is not None else 0.0
            old_status = old['status'] if old and 'status' in old.keys() else None
            user_id = old['user_id'] if old and 'user_id' in old.keys() else None

            # update investment row; preserve existing status when possible
            cur.execute('UPDATE investments SET current_profit = ?, status = ? WHERE id = ?', (new_profit, old_status or 'active', inv_id))
        except sqlite3.OperationalError:
            # add column then retry
            try:
                cur.execute('ALTER TABLE investments ADD COLUMN current_profit REAL')
                conn.commit()
            except Exception:
                pass
            cur.execute('SELECT current_profit, status, user_id FROM investments WHERE id = ?', (inv_id,))
            old = cur.fetchone()
            old_profit = float(old['current_profit']) if old and old['current_profit'] is not None else 0.0
            old_status = old['status'] if old and 'status' in old.keys() else None
            user_id = old['user_id'] if old and 'user_id' in old.keys() else None
            cur.execute('UPDATE investments SET current_profit = ?, status = ? WHERE id = ?', (new_profit, old_status or 'active', inv_id))

        # adjust user's balance by delta
        try:
            delta = 0.0
            if new_profit is not None:
                delta = float(new_profit) - float(old_profit)
            if user_id and delta != 0:
                cur.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
                u = cur.fetchone()
                if u:
                    new_bal = (u['balance'] or 0.0) + delta
                    cur.execute('UPDATE users SET balance = ? WHERE id = ?', (new_bal, user_id))
        except Exception:
            pass

        conn.commit()
        conn.close()
        flash('Investment updated', 'success')
        return redirect(url_for('admin_dashboard'))

    conn.close()
    return render_template('admin/investment_form.html', inv=inv_dict)

@app.route('/admin/reject_withdrawal/<int:wid>', methods=['POST'])
@login_required
@admin_required
def reject_withdrawal(wid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE withdrawals SET status = ? WHERE id = ?', ('rejected', wid))
    conn.commit()
    conn.close()
    flash('Withdrawal rejected', 'info')
    return redirect(url_for('admin_dashboard'))


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# Admin: Announcements CRUD
@app.route('/admin/announcements')
@login_required
@admin_required
def admin_announcements():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM announcements ORDER BY id DESC')
        items = cur.fetchall()
    except Exception:
        items = []
    conn.close()
    return render_template('admin/announcements.html', announcements=items)


@app.route('/admin/announcements/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_announcements_new():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        video_url = request.form.get('video_url')
        display_type = request.form.get('display_type') or 'slider'
        is_active = 1 if request.form.get('is_active') == 'on' else 0
        start_date = request.form.get('start_date') or None
        end_date = request.form.get('end_date') or None
        image_filename = None
        # allow pre-uploaded video filename from async uploader
        video_filename = request.form.get('video_file') or None
        # handle image upload
        if 'image' in request.files and request.files['image'].filename:
            img = request.files['image']
            filename = secure_filename(img.filename)
            unique = f"{uuid.uuid4().hex}_{filename}"
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique)
            try:
                img_obj = Image.open(img.stream)
                img_obj.verify()
                img.stream.seek(0)
                img.save(save_path)
                image_filename = unique
            except Exception:
                flash('Invalid image uploaded', 'danger')
                return redirect(url_for('admin_announcements_new'))
        # handle video upload (optional) - only if not already uploaded via async uploader
        if not video_filename and 'video' in request.files and request.files['video'].filename:
            vid = request.files['video']
            vname = secure_filename(vid.filename)
            vext = os.path.splitext(vname)[1].lower()
            ALLOWED_VIDEO = ('.mp4', '.webm', '.ogg')
            if vext not in ALLOWED_VIDEO:
                flash('Invalid video type. Allowed: mp4, webm, ogg', 'danger')
                return redirect(url_for('admin_announcements_new'))
            unique_v = f"{uuid.uuid4().hex}_{vname}"
            save_v = os.path.join(app.config['UPLOAD_FOLDER'], unique_v)
            # stream-save with size check
            try:
                total = 0
                with open(save_v, 'wb') as outp:
                    chunk = vid.stream.read(8192)
                    while chunk:
                        total += len(chunk)
                        if total > app.config['MAX_VIDEO_FILE_SIZE']:
                            outp.close()
                            os.remove(save_v)
                            flash('Video exceeds maximum allowed size of 20MB', 'danger')
                            return redirect(url_for('admin_announcements_new'))
                        outp.write(chunk)
                        chunk = vid.stream.read(8192)
                video_filename = unique_v
            except Exception:
                try:
                    if os.path.exists(save_v):
                        os.remove(save_v)
                except Exception:
                    pass
                flash('Failed to save video', 'danger')
                return redirect(url_for('admin_announcements_new'))
        conn = get_db()
        cur = conn.cursor()
        # try to insert with new columns, fallback if table older
        try:
            cur.execute('INSERT INTO announcements (title, content, image_url, video_url, video_file, display_type, is_active, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (title, content, image_filename, video_url, video_filename, display_type, is_active, start_date, end_date, datetime.utcnow().isoformat()))
        except sqlite3.OperationalError:
            cur.execute('INSERT INTO announcements (title, content, image_url, video_url, is_active, start_date, end_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (title, content, image_filename, video_url, is_active, start_date, end_date, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
        flash('Announcement created', 'success')
        return redirect(url_for('admin_announcements'))
    return render_template('admin/announcement_form.html', announcement=None)


@app.route('/admin/announcements/<int:ann_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_announcements_edit(ann_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM announcements WHERE id = ?', (ann_id,))
    ann = cur.fetchone()
    if not ann:
        conn.close()
        flash('Announcement not found', 'danger')
        return redirect(url_for('admin_announcements'))
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        video_url = request.form.get('video_url')
        display_type = request.form.get('display_type') or (ann['display_type'] if 'display_type' in ann.keys() else 'slider')
        is_active = 1 if request.form.get('is_active') == 'on' else 0
        start_date = request.form.get('start_date') or None
        end_date = request.form.get('end_date') or None
        image_filename = ann['image_url']
        video_filename = request.form.get('video_file') or (ann['video_file'] if 'video_file' in ann.keys() else None)
        # handle image upload
        if 'image' in request.files and request.files['image'].filename:
            img = request.files['image']
            filename = secure_filename(img.filename)
            unique = f"{uuid.uuid4().hex}_{filename}"
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique)
            try:
                img_obj = Image.open(img.stream)
                img_obj.verify()
                img.stream.seek(0)
                img.save(save_path)
                image_filename = unique
            except Exception:
                flash('Invalid image uploaded', 'danger')
                return redirect(url_for('admin_announcements_edit', ann_id=ann_id))
        # handle video upload (optional)
        if not video_filename and 'video' in request.files and request.files['video'].filename:
            vid = request.files['video']
            vname = secure_filename(vid.filename)
            vext = os.path.splitext(vname)[1].lower()
            ALLOWED_VIDEO = ('.mp4', '.webm', '.ogg')
            if vext not in ALLOWED_VIDEO:
                flash('Invalid video type. Allowed: mp4, webm, ogg', 'danger')
                return redirect(url_for('admin_announcements_edit', ann_id=ann_id))
            unique_v = f"{uuid.uuid4().hex}_{vname}"
            save_v = os.path.join(app.config['UPLOAD_FOLDER'], unique_v)
            # stream-save with size check
            try:
                total = 0
                with open(save_v, 'wb') as outp:
                    chunk = vid.stream.read(8192)
                    while chunk:
                        total += len(chunk)
                        if total > app.config['MAX_VIDEO_FILE_SIZE']:
                            outp.close()
                            os.remove(save_v)
                            flash('Video exceeds maximum allowed size of 20MB', 'danger')
                            return redirect(url_for('admin_announcements_edit', ann_id=ann_id))
                        outp.write(chunk)
                        chunk = vid.stream.read(8192)
                video_filename = unique_v
            except Exception:
                try:
                    if os.path.exists(save_v):
                        os.remove(save_v)
                except Exception:
                    pass
                flash('Failed to save video', 'danger')
                return redirect(url_for('admin_announcements_edit', ann_id=ann_id))
        try:
            cur.execute('UPDATE announcements SET title = ?, content = ?, image_url = ?, video_url = ?, video_file = ?, display_type = ?, is_active = ?, start_date = ?, end_date = ? WHERE id = ?',
                        (title, content, image_filename, video_url, video_filename, display_type, is_active, start_date, end_date, ann_id))
        except sqlite3.OperationalError:
            cur.execute('UPDATE announcements SET title = ?, content = ?, image_url = ?, video_url = ?, is_active = ?, start_date = ?, end_date = ? WHERE id = ?',
                        (title, content, image_filename, video_url, is_active, start_date, end_date, ann_id))
        conn.commit()
        conn.close()
        flash('Announcement updated', 'success')
        return redirect(url_for('admin_announcements'))
    conn.close()
    return render_template('admin/announcement_form.html', announcement=ann)


@app.route('/admin/announcements/upload_video', methods=['POST'])
@login_required
@admin_required
def admin_announcements_upload_video():
    if 'video' not in request.files:
        return {'error': 'No file part'}, 400
    vid = request.files['video']
    if vid.filename == '':
        return {'error': 'No selected file'}, 400
    vname = secure_filename(vid.filename)
    vext = os.path.splitext(vname)[1].lower()
    ALLOWED_VIDEO = ('.mp4', '.webm', '.ogg')
    if vext not in ALLOWED_VIDEO:
        return {'error': 'Invalid video type'}, 400
    unique_v = f"{uuid.uuid4().hex}_{vname}"
    save_v = os.path.join(app.config['UPLOAD_FOLDER'], unique_v)
    # stream-save with size check
    try:
        total = 0
        with open(save_v, 'wb') as outp:
            chunk = vid.stream.read(8192)
            while chunk:
                total += len(chunk)
                if total > app.config['MAX_VIDEO_FILE_SIZE']:
                    outp.close()
                    os.remove(save_v)
                    return {'error': 'File too large'}, 400
                outp.write(chunk)
                chunk = vid.stream.read(8192)
    except Exception as e:
        try:
            if os.path.exists(save_v):
                os.remove(save_v)
        except Exception:
            pass
        return {'error': 'Failed to save'}, 500
    return {'filename': unique_v, 'url': url_for('uploaded_file', filename=unique_v)}


@app.route('/admin/announcements/<int:ann_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_announcements_delete(ann_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM announcements WHERE id = ?', (ann_id,))
    conn.commit()
    conn.close()
    flash('Announcement deleted', 'info')
    return redirect(url_for('admin_announcements'))


@app.route('/admin/announcements/<int:ann_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_announcements_toggle(ann_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT is_active FROM announcements WHERE id = ?', (ann_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash('Announcement not found', 'danger')
        return redirect(url_for('admin_announcements'))
    new_status = 0 if row['is_active'] == 1 else 1
    cur.execute('UPDATE announcements SET is_active = ? WHERE id = ?', (new_status, ann_id))
    conn.commit()
    conn.close()
    flash('Announcement status updated', 'success')
    return redirect(url_for('admin_announcements'))


# --- Assistant API & Admin ---
@app.route('/assistant/config')
def assistant_config():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM assistant_config WHERE id = 1')
        cfg = cur.fetchone()
        if cfg:
            return jsonify({
                'enabled': bool(cfg['enabled']),
                'button_label': cfg['button_label'] or 'Help',
                'assistant_name': cfg['assistant_name'] or 'Assistant',
                'avatar_url': cfg['avatar_url']
            })
    except Exception:
        pass
    return jsonify({'enabled': False})


@app.route('/assistant/start')
def assistant_start():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM assistant_nodes WHERE is_root = 1 LIMIT 1')
        node = cur.fetchone()
        if not node:
            return jsonify({'error': 'No assistant configured'}), 404
        cur.execute('SELECT * FROM assistant_options WHERE node_id = ? ORDER BY display_order', (node['id'],))
        opts = cur.fetchall()
        return jsonify({'node': dict(node), 'options': [dict(o) for o in opts]})
    except Exception:
        return jsonify({'error': 'failed'}), 500


@app.route('/assistant/node/<int:node_id>')
def assistant_node(node_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM assistant_nodes WHERE id = ?', (node_id,))
    node = cur.fetchone()
    if not node:
        return jsonify({'error': 'not found'}), 404
    cur.execute('SELECT * FROM assistant_options WHERE node_id = ? ORDER BY display_order', (node_id,))
    opts = cur.fetchall()
    return jsonify({'node': dict(node), 'options': [dict(o) for o in opts]})


@app.route('/assistant/log', methods=['POST'])
def assistant_log():
    data = request.get_json() or {}
    node_id = data.get('node_id')
    option_id = data.get('option_id')
    user_id = data.get('user_id')
    metadata = data.get('metadata')
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO assistant_logs (node_id, option_id, user_id, metadata, created_at) VALUES (?, ?, ?, ?, ?)',
                    (node_id, option_id, user_id, metadata, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    return jsonify({'status': 'ok'})


def _simple_assistant_reply(message):
    m = (message or '').lower()
    if any(x in m for x in ('hello', 'hi', 'hey')):
        return 'Hello — I can help you compare plans, explain durations, and recommend starting amounts.'
    if 'recommend' in m or 'plan' in m:
        return 'Try the Starter Plan: minimum $100, return ~10% over 30 days. Ask for alternatives by risk level.'
    if 'how' in m and 'invest' in m:
        return 'Start small, diversify across plans, and reinvest profits cautiously.'
    if '?' in message:
        return 'That is a good question. Could you please give a few more details so I can answer precisely?'
    return 'I can help with investment plans, returns, and account questions. Ask me something specific.'


@app.route('/assistant/query', methods=['POST'])
def assistant_query():
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'empty'}), 400

    user_id = data.get('user_id') or session.get('user_id')

    # If OPENAI_API_KEY is set, attempt to use it. Otherwise fall back to a simple local reply.
    api_key = os.environ.get('OPENAI_API_KEY')
    model = os.environ.get('OPENAI_MODEL', 'gpt-3.5-turbo')
    reply = None
    if api_key:
        payload = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': 'You are an investment assistant. Answer concisely and safely.'},
                {'role': 'user', 'content': message}
            ],
            'max_tokens': 500
        }
        try:
            req = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=json.dumps(payload).encode('utf-8'),
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.load(resp)
                if isinstance(res, dict) and res.get('choices'):
                    choice = res['choices'][0]
                    if isinstance(choice, dict) and choice.get('message'):
                        reply = choice['message'].get('content', '')
        except Exception:
            # fall through to simple reply on any error
            reply = None

    if not reply:
        reply = _simple_assistant_reply(message)

    # Log the user query to assistant_logs for analytics
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO assistant_logs (node_id, option_id, user_id, metadata, created_at) VALUES (?, ?, ?, ?, ?)',
                    (None, None, user_id, json.dumps({'message': message, 'reply': reply}), datetime.utcnow().isoformat()))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    return jsonify({'reply': reply})


@app.route('/assistant/plans')
def assistant_plans():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, plan_name, minimum_amount, profit_amount, total_return, duration_days FROM investment_plans WHERE status = 'active' ORDER BY id")
        rows = cur.fetchall()
        plans = [dict(r) for r in rows]
        return jsonify({'plans': plans})
    except Exception:
        return jsonify({'plans': []})


@app.route('/assistant/testimonials')
def assistant_testimonials():
    # Prefer testimonials from DB if table exists, otherwise fall back to static list
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name AS title, body FROM testimonials ORDER BY id DESC")
        rows = cur.fetchall()
        if rows:
            return jsonify({'testimonials': [dict(r) for r in rows]})
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    testimonials = [
        {'title': 'John M.', 'body': 'Turned $200 into consistent weekly profits.'},
        {'title': 'Sarah K.', 'body': 'Recovered her starting capital in 3 weeks.'},
        {'title': 'David A.', 'body': 'Upgraded from Starter to Gold within a month.'}
    ]
    return jsonify({'testimonials': testimonials})


@app.route('/assistant/info')
def assistant_info():
    desc = 'This program helps members participate in our trading and investment system. Members choose a plan, activate their account, and monitor progress from their dashboard. Our goal is to make the process simple, transparent, and rewarding.'
    return jsonify({'description': desc})


@app.route('/assistant/contact')
def assistant_contact():
    # Pull admin contact from config file first, then env
    contact = read_admin_contact()
    name = contact.get('name') or os.environ.get('ADMIN_NAME', 'Mr. Simon')
    phone = contact.get('phone') or os.environ.get('ADMIN_PHONE', '+234XXXXXXXXX')
    whatsapp_raw = contact.get('whatsapp') or os.environ.get('ADMIN_WHATSAPP', '')
    if whatsapp_raw:
        if whatsapp_raw.startswith('https://'):
            wa = whatsapp_raw
        else:
            wa = 'https://wa.me/' + whatsapp_raw.replace('+', '').replace(' ', '')
    else:
        wa = ''
    return jsonify({'name': name, 'phone': phone, 'whatsapp': wa})


@app.context_processor
def inject_admin_contact():
    # expose admin contact info to all templates (reads from config file first, then env)
    cfg_dir = os.path.join(BASE_DIR, 'config')
    cfg_file = os.path.join(cfg_dir, 'admin_contact.json')
    contact = {}
    try:
        if os.path.exists(cfg_file):
            with open(cfg_file, 'r', encoding='utf-8') as f:
                contact = json.load(f) or {}
    except Exception:
        contact = {}

    name = contact.get('name') or os.environ.get('ADMIN_NAME', 'Mr. Simon')
    phone = contact.get('phone') or os.environ.get('ADMIN_PHONE', '+16727023654')
    whatsapp_raw = contact.get('whatsapp') or os.environ.get('ADMIN_WHATSAPP', '')
    if whatsapp_raw:
        if whatsapp_raw.startswith('https://'):
            wa = whatsapp_raw
        else:
            wa = 'https://wa.me/' + whatsapp_raw.replace('+', '').replace(' ', '')
    else:
        wa = ''
    return dict(ADMIN_NAME=name, ADMIN_PHONE=phone, ADMIN_WHATSAPP_URL=wa, ADMIN_WHATSAPP_RAW=whatsapp_raw, ADMIN_CONTACT=contact)


def _ensure_config_dir():
    cfg_dir = os.path.join(BASE_DIR, 'config')
    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def read_admin_contact():
    cfg_dir = _ensure_config_dir()
    cfg_file = os.path.join(cfg_dir, 'admin_contact.json')
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}


def write_admin_contact(data):
    cfg_dir = _ensure_config_dir()
    cfg_file = os.path.join(cfg_dir, 'admin_contact.json')
    try:
        with open(cfg_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


@app.route('/admin/assistant/logs')
@login_required
@admin_required
def admin_assistant_logs():
    node_id = request.args.get('node_id')
    option_id = request.args.get('option_id')
    user_id = request.args.get('user_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    PER_PAGE = 50
    conn = get_db()
    cur = conn.cursor()
    # build where clauses
    where = 'WHERE 1=1'
    params = []
    if node_id:
        where += ' AND l.node_id = ?'
        params.append(node_id)
    if option_id:
        where += ' AND l.option_id = ?'
        params.append(option_id)
    if user_id:
        where += ' AND l.user_id = ?'
        params.append(user_id)
    if start_date:
        where += ' AND l.created_at >= ?'
        params.append(start_date)
    if end_date:
        where += ' AND l.created_at <= ?'
        params.append(end_date)

    count_sql = 'SELECT COUNT(*) as cnt FROM assistant_logs l LEFT JOIN assistant_nodes a ON l.node_id = a.id LEFT JOIN assistant_options o ON l.option_id = o.id ' + where
    try:
        cur.execute(count_sql, params)
        total = cur.fetchone()['cnt']

        offset = (page - 1) * PER_PAGE
        sql = '''SELECT l.*, a.question as node_question, o.option_text as option_text
                 FROM assistant_logs l
                 LEFT JOIN assistant_nodes a ON l.node_id = a.id
                 LEFT JOIN assistant_options o ON l.option_id = o.id
        ''' + where + ' ORDER BY l.created_at DESC LIMIT ? OFFSET ?'
        exec_params = list(params) + [PER_PAGE, offset]
        cur.execute(sql, exec_params)
        rows = cur.fetchall()
        # fetch nodes/options for filters
        cur.execute('SELECT id, question FROM assistant_nodes ORDER BY id')
        nodes = cur.fetchall()
        cur.execute('SELECT id, option_text FROM assistant_options ORDER BY id')
        options = cur.fetchall()
    except sqlite3.OperationalError:
        # missing tables or schema; present empty results instead of crashing
        rows = []
        nodes = []
        options = []
        total = 0
    finally:
        conn.close()

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template('admin/assistant_logs.html', rows=rows, nodes=nodes, options=options, filters=request.args, page=page, total_pages=total_pages)


@app.route('/admin/assistant/logs/export')
@login_required
@admin_required
def admin_assistant_logs_export():
    node_id = request.args.get('node_id')
    option_id = request.args.get('option_id')
    user_id = request.args.get('user_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    conn = get_db()
    cur = conn.cursor()
    sql = '''SELECT l.*, a.question as node_question, o.option_text as option_text
             FROM assistant_logs l
             LEFT JOIN assistant_nodes a ON l.node_id = a.id
             LEFT JOIN assistant_options o ON l.option_id = o.id
             WHERE 1=1'''
    params = []
    if node_id:
        sql += ' AND l.node_id = ?'
        params.append(node_id)
    if option_id:
        sql += ' AND l.option_id = ?'
        params.append(option_id)
    if user_id:
        sql += ' AND l.user_id = ?'
        params.append(user_id)
    if start_date:
        sql += ' AND l.created_at >= ?'
        params.append(start_date)
    if end_date:
        sql += ' AND l.created_at <= ?'
        params.append(end_date)
    sql += ' ORDER BY l.created_at DESC'
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        # missing tables; return empty CSV
        rows = []

    # build CSV
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id','created_at','user_id','node_id','node_question','option_id','option_text','metadata'])
    for r in rows:
        # r may be sqlite3.Row or dict-like
        try:
            node_q = r.get('node_question') if hasattr(r, 'get') else (r['node_question'] if 'node_question' in r.keys() else None)
        except Exception:
            node_q = None
        try:
            opt_text = r.get('option_text') if hasattr(r, 'get') else (r['option_text'] if 'option_text' in r.keys() else None)
        except Exception:
            opt_text = None
        cw.writerow([r['id'], r['created_at'], r['user_id'], r['node_id'], node_q, r['option_id'], opt_text, r['metadata']])
    csv_content = si.getvalue()
    # persist export file for history
    filename = f"assistant_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.csv"
    save_path = os.path.join(EXPORT_FOLDER, filename)
    try:
        with open(save_path, 'w', encoding='utf-8', newline='') as f:
            f.write(csv_content)
        # record in assistant_exports table if it exists
        try:
            filters_json = json.dumps(dict(request.args))
            cur.execute('INSERT INTO assistant_exports (filename, filters, created_at) VALUES (?, ?, ?)', (filename, filters_json, datetime.utcnow().isoformat()))
            conn.commit()
        except Exception:
            # if table missing or insert fails, ignore
            conn.rollback()
    except Exception:
        # if saving file fails, continue to return CSV directly
        pass
    output = make_response(csv_content)
    output.headers['Content-Type'] = 'text/csv'
    output.headers['Content-Disposition'] = f'attachment; filename={filename}'
    conn.close()
    return output


@app.route('/admin/assistant/exports')
@login_required
@admin_required
def admin_assistant_exports():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM assistant_exports ORDER BY id DESC')
        exports = cur.fetchall()
    except Exception:
        exports = []
    conn.close()
    return render_template('admin/assistant_exports.html', exports=exports)


@app.route('/admin/assistant')
@login_required
@admin_required
def admin_assistant_list():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM assistant_nodes ORDER BY id DESC')
    nodes = cur.fetchall()
    conn.close()
    return render_template('admin/assistant_list.html', nodes=nodes)


@app.route('/admin/assistant/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_assistant_new():
    if request.method == 'POST':
        question = request.form.get('question')
        is_root = 1 if request.form.get('is_root') == 'on' else 0
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO assistant_nodes (question, is_root, created_at) VALUES (?, ?, ?)', (question, is_root, datetime.utcnow().isoformat()))
        nid = cur.lastrowid
        # insert options
        texts = request.form.getlist('option_text[]')
        nexts = request.form.getlist('option_next[]')
        actions = request.form.getlist('option_action[]')
        payloads = request.form.getlist('option_payload[]')
        for i, t in enumerate(texts):
            if not t.strip():
                continue
            nxt = int(nexts[i]) if nexts and i < len(nexts) and nexts[i].isdigit() else None
            act = actions[i] if actions and i < len(actions) else None
            pay = payloads[i] if payloads and i < len(payloads) else None
            cur.execute('INSERT INTO assistant_options (node_id, option_text, next_node_id, action_type, action_payload, display_order) VALUES (?, ?, ?, ?, ?, ?)',
                        (nid, t, nxt, act, pay, i))
        conn.commit()
        conn.close()
        flash('Assistant node created', 'success')
        return redirect(url_for('admin_assistant_list'))
    # fetch nodes for possible next targets
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, question FROM assistant_nodes ORDER BY id')
    nodes = cur.fetchall()
    conn.close()
    return render_template('admin/assistant_form.html', nodes=nodes, node=None)


@app.route('/admin/assistant/<int:node_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_assistant_edit(node_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM assistant_nodes WHERE id = ?', (node_id,))
    node = cur.fetchone()
    if not node:
        conn.close()
        flash('Node not found', 'danger')
        return redirect(url_for('admin_assistant_list'))
    if request.method == 'POST':
        question = request.form.get('question')
        is_root = 1 if request.form.get('is_root') == 'on' else 0
        cur.execute('UPDATE assistant_nodes SET question = ?, is_root = ? WHERE id = ?', (question, is_root, node_id))
        # replace options
        cur.execute('DELETE FROM assistant_options WHERE node_id = ?', (node_id,))
        texts = request.form.getlist('option_text[]')
        nexts = request.form.getlist('option_next[]')
        actions = request.form.getlist('option_action[]')
        payloads = request.form.getlist('option_payload[]')
        for i, t in enumerate(texts):
            if not t.strip():
                continue
            nxt = int(nexts[i]) if nexts and i < len(nexts) and nexts[i].isdigit() else None
            act = actions[i] if actions and i < len(actions) else None
            pay = payloads[i] if payloads and i < len(payloads) else None
            cur.execute('INSERT INTO assistant_options (node_id, option_text, next_node_id, action_type, action_payload, display_order) VALUES (?, ?, ?, ?, ?, ?)',
                        (node_id, t, nxt, act, pay, i))
        conn.commit()
        conn.close()
        flash('Node updated', 'success')
        return redirect(url_for('admin_assistant_list'))
    cur.execute('SELECT * FROM assistant_options WHERE node_id = ? ORDER BY display_order', (node_id,))
    options = cur.fetchall()
    cur.execute('SELECT id, question FROM assistant_nodes ORDER BY id')
    nodes = cur.fetchall()
    conn.close()
    return render_template('admin/assistant_form.html', node=node, options=options, nodes=nodes)


@app.route('/admin/assistant/<int:node_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_assistant_delete(node_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM assistant_options WHERE node_id = ?', (node_id,))
    cur.execute('DELETE FROM assistant_nodes WHERE id = ?', (node_id,))
    conn.commit()
    conn.close()
    flash('Node deleted', 'info')
    return redirect(url_for('admin_assistant_list'))

if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        init_db()
    app.run(debug=True)

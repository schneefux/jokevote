#!/usr/bin/python3
import hashlib
import os
import re
from config import config
from flask import (
    Flask,
    render_template,
    make_response,
    Markup,
    request,
    abort,
    g,
    send_from_directory,
    redirect,
    session,
    flash
)
import sqlite3


class dbProxy(object):
    def __init__(self, db, rootName):
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = self.dict_factory
        self.c = self.conn.cursor()

        self.prefix = "v1b"
        if self.database_v() == '0':
            self.migrate_v0to1()
        if self.database_v() == '1':
            self.migrate_v1to1a()
        if self.database_v() == '1a':
            self.migrate_v1ato1b()
        if self.database_v() == '-1':
            self.create_v1a()

        self.rootUser(rootName)

        self.tagmark = "_"

    def close(self):
        self.conn.close()

    def database_v(self):
        versions = {
            'jokes': '0',
            'v1_jokes': '1',
            'v1a_jokes': '1a',
            'v1b_jokes': '1b'
        }
        for ver in versions:
            if self.c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (ver,)).fetchone()['COUNT(*)'] == 1:
                return versions[ver]
        return '-1'

    def dict_factory(self, cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d

    def prettifyText(self, text):
        html = re.sub('<[^<]+?>', '', text)
        html = re.sub(r"/(\w+)/", '<em>\\1</em>', html)
        html = re.sub(r"\*(\w+)\*", '<strong>\\1</strong>', html)
        html = re.sub(r"#(\w+)", '<a href="/?filter=' + self.tagmark + '\\1">#\\1</a>', html)
        html = html.replace("\n", "<br />")
        return html

    def create_v0(self):
        app.logger.warning("creating new v0 database")
        self.c.execute("CREATE TABLE jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, upvotes INTEGER, downvotes INTEGER, reports INTEGER)")
        self.c.execute("CREATE TABLE votes(id INTEGER PRIMARY KEY NOT NULL, ip TEXT, jokeid INTEGER, type INTEGER)")
        self.conn.commit()

    def create_v1(self):
        app.logger.warning("creating new v1 database")
        self.c.execute("CREATE TABLE v1_jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE v1_users(id INTEGER PRIMARY KEY NOT NULL, identifier TEXT)")
        self.c.execute("CREATE TABLE v1_votes(id INTEGER PRIMARY KEY NOT NULL, joke INTEGER, user INTEGER, type TEXT)")
        self.conn.commit()

    def create_v1a(self):
        app.logger.warning("creating new " + self.prefix + " database")
        self.c.execute("CREATE TABLE " + self.prefix + "_jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE " + self.prefix + "_votes(id INTEGER PRIMARY KEY NOT NULL, joke INTEGER, user INTEGER, type TEXT)")
        self.c.execute("CREATE TABLE " + self.prefix + "_users(id INTEGER PRIMARY KEY NOT NULL, identifier TEXT, role TEXT DEFAULT 'guest', password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.conn.commit()

    def create_v1b(self):
        create_v1a()

    def migrate_v0to1(self):
        app.logger.warning("migrating database from v0 to v1")
        self.create_v1()

        self.c.execute("INSERT INTO v1_users(identifier) VALUES ('anonymous')")
        anonymous = self.c.lastrowid

        # create new jokes
        j = self.c.execute("SELECT * FROM jokes").fetchall()
        j = [(int(n['id']), n['text'], anonymous) for n in j]
        self.c.executemany("INSERT INTO v1_jokes(id, text, format, user) VALUES(?, ?, 'html', ?)", j)

        votes = self.c.execute("SELECT * FROM votes").fetchall()

        # create new users
        ips = set([v['ip'] for v in votes])  # uniquify
        ips = [(ip,) for ip in ips]
        self.c.executemany("INSERT INTO v1_users(identifier) VALUES(?)", ips)

        # create votes
        for vote in votes:
            user = int(self.c.execute("SELECT id FROM v1_users WHERE identifier=?", (vote['ip'],)).fetchone()['id'])
            if vote['type'] == -1:
                vtype = 'down'
            if vote['type'] == 0:
                vtype = 'report'
            if vote['type'] == 1:
                vtype = 'up'
            self.c.execute("INSERT INTO v1_votes(joke, user, type) VALUES(?, ?, ?)", (vote['jokeid'], user, vtype))

        # create pre-v0 votes
        jokes = self.c.execute("SELECT id FROM v1_jokes").fetchall()
        jokes = [int(j['id']) for j in jokes]
        for joke in jokes:
            types = (
                ("up", "upvotes"),
                ("down", "downvotes"),
                ("report", "reports")
            )
            for newtype, oldtype in types:
                legal_votes = int(self.c.execute("SELECT COUNT(*) FROM v1_votes WHERE id=? AND type=?", (joke, newtype)).fetchone()['COUNT(*)'])
                real_votes = int(self.c.execute("SELECT * FROM jokes WHERE id=?", (joke,)).fetchone()[oldtype])
                diff = real_votes - legal_votes
                if diff:
                    self.c.executemany("INSERT INTO v1_votes(joke, user, type) VALUES(?, ?, ?)", [(joke, anonymous, newtype)]*diff)

        # drop old tables
        self.c.execute("DROP TABLE jokes")
        self.c.execute("DROP TABLE IF EXISTS votes")

        # write
        self.conn.commit()

    def migrate_v1to1a(self):
        app.logger.warning("migrating database from v1 to v1a")
        self.c.execute("ALTER TABLE v1_jokes RENAME TO v1a_jokes")
        self.c.execute("ALTER TABLE v1_votes RENAME TO v1a_votes")
        self.c.execute("CREATE TABLE v1a_users(id INTEGER PRIMARY KEY, identifier TEXT, role TEXT DEFAULT 'guest', password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.c.execute("INSERT INTO v1a_users(id, identifier) SELECT id, identifier FROM v1_users")
        self.c.execute("DROP TABLE v1_users")
        self.conn.commit()

    def migrate_v1ato1b(self):
        app.logger.warning("migrating database from v1a to v1b")
        self.c.execute("ALTER TABLE v1a_jokes RENAME TO v1b_jokes")
        self.c.execute("ALTER TABLE v1a_votes RENAME TO v1b_votes")
        self.c.execute("ALTER TABLE v1a_users RENAME TO v1b_users")
        self.c.execute("UPDATE v1b_jokes SET format='prettytext' WHERE format='markdown'")
        self.c.execute("UPDATE v1b_votes SET type='down' WHERE type='report'")
        self.conn.commit()

    def getJokes(self, user=None, filter=None, sortby='rank'):
        # user: return with user-specific attributes, also return deleted jokes
        # filter: return jokes including the specified word
        # sortby: rank - calculated by score and freshness, score - only score
        ret_jokes = []
        jokes = self.c.execute("SELECT * FROM " + self.prefix + "_jokes ORDER BY id ASC").fetchall()
        votewhere = "SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE "
        users = self.c.execute("SELECT id FROM " + self.prefix + "_users WHERE role='user'").fetchall()
        users = [u['id'] for u in users]
        iamroot = not self.c.execute("SELECT COUNT(*) FROM " + self.prefix + "_users WHERE id=? AND role='super'", (user,)).fetchone()['COUNT(*)'] == 0
        for joke in jokes:
            ret_joke = {
                'id': joke['id']
            }
            # mark jokes the user has already interacted with
            ret_joke['upvoted'] = not self.c.execute(votewhere + "joke=? AND user=? AND type='up'", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            ret_joke['downvoted'] = not self.c.execute(votewhere + "joke=? AND user=? AND type='down'", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            # allow deletion
            ret_joke['mine'] = (joke['user'] == user or iamroot)

            if joke['format'] == 'prettytext':
                ret_joke['html'] = self.prettifyText(joke['text'])
                ret_joke['text'] = joke['text']
            if joke['format'] == 'html':
                ret_joke['html'] = joke['text']
                ret_joke['text'] = re.sub('<[^<]+?>', '', joke['text'])

            if filter != None:  # simple search
                filter = re.sub(r"^" + self.tagmark, "#", filter)
                if filter.lower() not in ret_joke['text'].lower():
                    continue

            # actual scoring
            # TODO optimize queries
            score = 0
            score += self.c.execute(votewhere + "joke=? AND type='up'", (joke['id'],)).fetchone()['COUNT(*)']
            score -= self.c.execute(votewhere + "joke=? AND type='down'", (joke['id'],)).fetchone()['COUNT(*)']
            for u in users:
                # user's scores count 10 times more
                score += self.c.execute(votewhere + "joke=? AND type='up' AND user=?", (joke['id'], u)).fetchone()['COUNT(*)'] * 9
                score -= self.c.execute(votewhere + "joke=? AND type='down' AND user=?", (joke['id'], u)).fetchone()['COUNT(*)'] * 9

            ret_joke['score'] = score
            ret_joke['freshness'] = jokes[-1]['id'] - joke['id']

            # skip other's jokes marked as deleted
            if not self.c.execute("SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE type='delete' AND joke=?", (joke['id'],)).fetchone()['COUNT(*)'] == 0:
                # deleted
                if ret_joke['mine']:
                    ret_joke['deleted'] = True
                    ret_joke['score'] = ret_joke['freshness'] = -1000
                else:
                    continue

            ret_jokes.append(ret_joke)

        if sortby == 'rank':
            sorter = lambda j: (j['score']+1)/pow(j['freshness']+1, 2)
        if sortby == 'score':
            sorter = lambda j: j['score']

        ret_jokes = sorted(ret_jokes, key=sorter, reverse=True)
        return ret_jokes

    def addUser(self, name, password):
        if self.getUser(name=name) != -2:
            return -1  # already registered
        password = password.encode('utf-8')
        salt = os.urandom(32).hex().encode('utf-8')
        password = hashlib.sha512(password + salt).hexdigest()
        self.c.execute("INSERT INTO " + self.prefix + "_users(identifier, password, salt, role) VALUES(?, ?, ?, 'user')", (name, password, salt))
        self.conn.commit()
        return self.c.lastrowid

    def getUser(self, cookie=None, name=None, password=None):
        # cookie != None: return or create guest uid
        # name != None: return user uid or -2 (not found)
        # name, password != None: return user uid, -2 (not found) or -1 (wrong password)
        if cookie != None:
            user = self.c.execute("SELECT id FROM " + self.prefix + "_users WHERE role='guest' AND identifier=?", (cookie,)).fetchone()
            if user:  # existing guest
                uid = int(user['id'])
            else:  # create guest
                self.c.execute("INSERT INTO " + self.prefix + "_users(identifier) VALUES(?)", (cookie,))
                self.conn.commit()
                uid = self.c.lastrowid
        else:
            if name != None:
                user = self.c.execute("SELECT id FROM " + self.prefix + "_users WHERE identifier=?", (name,)).fetchone()
                if user:  # existing user
                    uid = int(user['id'])
                    if password != None:
                        password = password.encode('utf-8')
                        auth = self.c.execute("SELECT password, salt FROM " + self.prefix + "_users WHERE id=?", (uid,)).fetchone()
                        if hashlib.sha512(password + auth['salt']).hexdigest() != auth['password']:
                            uid = -1  # auth failed
                else:
                    uid = -2  # nonexistent

        return uid

    def rootUser(self, name):
        app.logger.warning("converting %s to superuser", name)
        self.c.execute("UPDATE " + self.prefix + "_users SET role='super' WHERE identifier=?", (name,))
        self.conn.commit()

    def addJoke(self, text, user):
        self.c.execute("INSERT INTO " + self.prefix + "_jokes(text, format, user) VALUES(?, 'prettytext', ?)", (text, user))
        self.conn.commit()

    def updateJoke(self, text, joke):
        self.c.execute("UPDATE " + self.prefix + "_jokes SET text=?, format='prettytext' WHERE id=?", (text, joke))
        self.conn.commit()

    def removeJoke(self, joke, user):
        self.c.execute("INSERT INTO " + self.prefix + "_votes(joke, user, type) VALUES(?, ?, 'delete')", (joke, user))
        self.conn.commit()

    def voteJoke(self, joke, down, user):
        self.c.execute("INSERT INTO " + self.prefix + "_votes(joke, user, type) VALUES(?, ?, ?)", (joke, user, 'down' if down else 'up'))
        self.conn.commit()

    def unvoteJoke(self, joke, user):
        self.c.execute("DELETE FROM " + self.prefix + "_votes WHERE joke=? AND user=?", (joke, user))
        self.conn.commit()

    def hasVoted(self, joke, user):
        return not self.c.execute("SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE joke=? AND user=?", (joke, user)).fetchone()['COUNT(*)'] == 0

    def mayModifyJoke(self, joke, user):
        if self.c.execute("SELECT role FROM " + self.prefix + "_users WHERE id=?", (user,)).fetchone()['role'] == 'super':
            return True
        if int(self.c.execute("SELECT user FROM " + self.prefix + "_jokes WHERE id=?", (joke,)).fetchone()['user']) == user:
            return True
        return False


def db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = dbProxy("votes.db", config['superuser'])
    return db

app = Flask(__name__)
PERPAGE = 10

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def userid():
    if 'userlogin' in session:
        uid = db().getUser(name=session['userlogin'])
        if uid != -2:
            return uid
    if not 'guestlogin' in session:
        session['guestlogin'] = os.urandom(32).hex()
    return db().getUser(cookie=session['guestlogin'])

@app.route('/')
def root():
    return page(0)

@app.route('/page/<int:num>')
def page(num):
    filter = request.args.get('filter')
    jokes = db().getJokes(user=userid(), filter=filter)
    user = {'loggedin': False}
    if 'userlogin' in session:
        user['loggedin'] = True
        user['name'] = session['userlogin']
    if filter:
        filter = re.sub(r"^" + db().tagmark, "#", filter)  # TODO find a cleaner way
    r = make_response(render_template('index.html', currentpage=num, tag=filter, perpage=PERPAGE, jokes=jokes, user=user))
    r.headers.set('X-SmoothState-Location', request.path)
    return r

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    db().addJoke(text, userid())
    return redirect(request.referrer)

@app.route('/edit', methods=['POST'])
def edit():
    text = request.form['text']
    joke = int(request.form['id'])
    if not db().mayModifyJoke(joke, userid()):
        abort(403)
    db().updateJoke(text, joke)
    return redirect(request.referrer)

@app.route('/upvote', methods=['POST'])
def upvote():
    joke = int(request.form['id'])
    if db().hasVoted(joke, userid()):
        db().unvoteJoke(joke, userid())
    db().voteJoke(joke, False, userid())
    return redirect(request.referrer)

@app.route('/downvote', methods=['POST'])
def downvote():
    joke = int(request.form['id'])
    if db().hasVoted(joke, userid()):
        db().unvoteJoke(joke, userid())
    db().voteJoke(joke, True, userid())
    return redirect(request.referrer)

@app.route('/delete', methods=['POST'])
def delete():
    joke = int(request.form['id'])
    if not db().mayModifyJoke(joke, userid()):
        abort(403)
    db().removeJoke(joke, userid())
    return redirect(request.referrer)

@app.route('/undelete', methods=['POST'])
def undelete():
    joke = int(request.form['id'])
    if not db().mayModifyJoke(joke, userid()):
        abort(403)
    db().unvoteJoke(joke, userid())
    return redirect(request.referrer)

@app.route('/login', methods=['POST'])
def login():
    if not 'guestlogin' in session:
        session['guestlogin'] = os.urandom(32).hex()
    cookie = session['guestlogin']
    name = request.form['user']
    pw = request.form['password']
    if db().addUser(name, pw) >= 0:
        flash('Erfolgreich registriert.')

    res = db().getUser(name=name, password=pw)
    if res == -2:
        flash('Benutzer existiert nicht.')
    if res == -1:
        flash('Passwort falsch.')
    if res >= 0:
        session['userlogin'] = name
        flash('Erfolgreich eingeloggt.')
    return redirect(request.referrer)

@app.route('/logout', methods=['POST'])
def logout():
    if 'userlogin' in session:
        del session['userlogin']
        flash('Ausgeloggt.')
    session['guestlogin'] = os.urandom(32).hex()
    return redirect(request.referrer)

@app.route('/export')
def export():
    filter = request.args.get('filter')
    jokes = db().getJokes(filter=filter, sortby='score')
    texts = [j['text'] for j in jokes]
    r = make_response("\n\r\n\r".join(texts))
    r.headers['Content-Type'] = 'text/plain; charset=utf-8';
    return r

# TODO use nginx for this
@app.route('/static/<path:path>')
def get_static(path):
    return send_from_directory('static', path)

@app.route('/robots.txt')
def robotstxt():
    return send_from_directory('static', 'robots.txt')

app.debug = config['debug']
app.secret_key = config['secret_key']
if __name__ == '__main__':
    app.run(host="0.0.0.0")

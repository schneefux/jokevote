#!/usr/bin/python3
import markdown
import hashlib
import os
import re
from config import config
from flask import (
    Flask,
    render_template,
    Markup,
    request,
    g,
    send_from_directory,
    redirect,
    session,
    flash
)
import sqlite3


class dbProxy(object):
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = self.dict_factory
        self.c = self.conn.cursor()

    def create_v0(self):
        self.c.execute("CREATE TABLE jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, upvotes INTEGER, downvotes INTEGER, reports INTEGER)")
        self.c.execute("CREATE TABLE votes(id INTEGER PRIMARY KEY NOT NULL, ip TEXT, jokeid INTEGER, type INTEGER)")
        self.conn.commit()

    def create_v1(self):
        self.c.execute("CREATE TABLE v1_jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE v1_users(id INTEGER PRIMARY KEY NOT NULL, identifier TEXT)")
        self.c.execute("CREATE TABLE v1_votes(id INTEGER PRIMARY KEY NOT NULL, joke INTEGER, user INTEGER, type TEXT)")
        self.conn.commit()

    def create_v1a(self):
        self.c.execute("CREATE TABLE v1a_jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE v1a_votes(id INTEGER PRIMARY KEY NOT NULL, joke INTEGER, user INTEGER, type TEXT)")
        self.c.execute("CREATE TABLE v1a_users(id INTEGER PRIMARY KEY NOT NULL, identifier TEXT, role TEXT DEFAULT 'guest', password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.conn.commit()

    def migrate_v1to1a(self):
        self.c.execute("ALTER TABLE v1_jokes RENAME TO v1a_jokes")
        self.c.execute("ALTER TABLE v1_votes RENAME TO v1a_votes")
        self.c.execute("CREATE TABLE v1a_users(id INTEGER PRIMARY KEY, identifier TEXT, role TEXT DEFAULT 'guest', password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.c.execute("INSERT INTO v1a_users(id, identifier) SELECT id, identifier FROM v1_users")
        self.c.execute("DROP TABLE v1_users")
        self.conn.commit()

    def migrate_v0to1(self):
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

    def database_v(self):
        versions = {
            'jokes': '0',
            'v1_jokes': '1',
            'v1a_jokes': '1a'
        }
        for ver in versions:
            if self.c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (ver,)).fetchone()['COUNT(*)'] == 1:
                return versions[ver]
        return '-1'

    def create(self):
        if self.database_v() == '0':
            print("migrating v0 to v1")
            self.migrate_v0to1()
        if self.database_v() == '1':
            print("migrating v1 to v1a")
            self.migrate_v1to1a()
        if self.database_v() == '-1':
            print("creating new v1a db")
            self.create_v1a()
        self.prefix = "v1a"

    def close(self):
        self.conn.close()

    def dict_factory(self, cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d

    def getPages(self, perpage):
        l = len(self.getJokes())-1
        return int(l/perpage)+1

    def getJokes(self, perpage=None, page=None, user=None):
        ret_jokes = []
        jokes = self.c.execute("SELECT * FROM " + self.prefix + "_jokes ORDER BY id ASC").fetchall()
        votewhere = "SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE "
        users = self.c.execute("SELECT id FROM " + self.prefix + "_users WHERE role='user'").fetchall()
        users = [u['id'] for u in users]
        for joke in jokes:
            # skip jokes marked as deleted
            if not self.c.execute("SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE type='delete' AND joke=?", (joke['id'],)).fetchone()['COUNT(*)'] == 0:
                continue

            ret_joke = {
                'id': joke['id']
            }
            # mark jokes the user has already interacted with
            ret_joke['upvoted'] = not self.c.execute(votewhere + "joke=? AND user=? AND type='up'", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            ret_joke['downvoted'] = not self.c.execute(votewhere + "joke=? AND user=? AND type='down'", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            ret_joke['reported'] = not self.c.execute(votewhere + "joke=? AND user=? AND type='report'", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            # allow deletion
            ret_joke['mine'] = joke['user'] == user
            # convert md->html if needed
            if joke['format'] == 'markdown':
                ret_joke['html'] = Markup(markdown.markdown(joke['text'], extensions=['markdown.extensions.nl2br'], output_format="html5", safe_mode="remove"))  # TODO safe_mode deprecated
                ret_joke['text'] = joke['text']
            if joke['format'] == 'html':
                ret_joke['html'] = joke['text']
                ret_joke['text'] = re.sub('<[^<]+?>', '', joke['text'])

            ret_joke['reports'] = self.c.execute(votewhere + "joke=? AND type='report'", (joke['id'],)).fetchone()['COUNT(*)']
            # actual scoring
            # TODO optimize queries
            score = 0
            score += self.c.execute(votewhere + "joke=? AND type='up'", (joke['id'],)).fetchone()['COUNT(*)']
            score -= self.c.execute(votewhere + "joke=? AND type='down'", (joke['id'],)).fetchone()['COUNT(*)']
            score -= self.c.execute(votewhere + "joke=? AND type='report'", (joke['id'],)).fetchone()['COUNT(*)'] * 5
            for u in users:
                # user's scores count 10 times more
                score += self.c.execute(votewhere + "joke=? AND type='up' AND user=?", (joke['id'], u)).fetchone()['COUNT(*)'] * 9
                score -= self.c.execute(votewhere + "joke=? AND type='down' AND user=?", (joke['id'], u)).fetchone()['COUNT(*)'] * 9
                score -= self.c.execute(votewhere + "joke=? AND type='report' AND user=?", (joke['id'], u)).fetchone()['COUNT(*)'] * 45

            ret_joke['score'] = score
            ret_joke['freshness'] = jokes[-1]['id'] - joke['id']
            ret_jokes.append(ret_joke)

        ret_jokes = sorted(ret_jokes, key=lambda j: (j['score']+1)/pow(j['freshness']+1, 2), reverse=True)
        if page != None and perpage != None:
            return ret_jokes[page*perpage:(page+1)*perpage]
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
                user = self.c.execute("SELECT id FROM " + self.prefix + "_users WHERE role='user' AND identifier=?", (name,)).fetchone()
                if user:  # existing user
                    uid = int(user['id'])
                    if password != None:
                        password = password.encode('utf-8')
                        auth = self.c.execute("SELECT password, salt FROM " + self.prefix + "_users WHERE id=?", (uid,)).fetchone()
                        if hashlib.sha512(password + auth['salt']).hexdigest() != auth['password']:
                            uid = -1  # auth failed
                else:
                    uid = -2 # nonexistent

        return uid

    def addJoke(self, text, user):
        self.c.execute("INSERT INTO " + self.prefix + "_jokes(text, format, user) VALUES(?, 'markdown', ?)", (text, user))
        self.conn.commit()

    def updateJoke(self, text, objectId):
        self.c.execute("UPDATE " + self.prefix + "_jokes SET text=? WHERE id=?", (text, objectId))
        self.conn.commit()

    def removeJoke(self, objectId, user):
        self.c.execute("INSERT INTO " + self.prefix + "_votes(joke, user, type) VALUES(?, ?, 'delete')", (objectId, user))
        self.conn.commit()

    def voteJoke(self, objectId, down, user):
        self.c.execute("INSERT INTO " + self.prefix + "_votes(joke, user, type) VALUES(?, ?, ?)", (objectId, user, 'down' if down else 'up'))
        self.conn.commit()

    def unvoteJoke(self, objectId, user):
        self.c.execute("DELETE FROM " + self.prefix + "_votes WHERE joke=? AND user=?", (objectId, user))
        self.conn.commit()

    def reportJoke(self, objectId, user):
        self.c.execute("INSERT INTO " + self.prefix + "_votes(joke, user, type) VALUES(?, ?, 'report')", (objectId, user))
        self.conn.commit()

    def getUserVotes(self, user):
        votes = self.c.execute("SELECT joke FROM " + self.prefix + "_votes WHERE user=?", (user,)).fetchall()
        votes = [v['joke'] for v in votes]
        return votes

    def getUserJokes(self, user):
        jokes = self.c.execute("SELECT id FROM " + self.prefix + "_jokes WHERE user=?", (user,)).fetchall()
        jokes = [j['id'] for j in jokes]
        return jokes


def db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = dbProxy("votes.db")
        db.create()
    return db

app = Flask(__name__)
PERPAGE = 10

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

@app.route('/')
def root():
    return page(0)

def userid():
    if 'userlogin' in session:
        uid = db().getUser(name=session['userlogin'])
        if uid != -2:
            return uid
    if not 'guestlogin' in session:
        session['guestlogin'] = os.urandom(32).hex()
    return db().getUser(cookie=session['guestlogin'])

@app.route('/page/<int:num>')
def page(num):
    numpages = db().getPages(PERPAGE)
    jokes = db().getJokes(PERPAGE, num, userid())
    user = {'loggedin': False}
    if 'userlogin' in session:
        user['loggedin'] = True
        user['name'] = session['userlogin']
    return render_template('index.html', currentpage=num, pages=[[]]*numpages, jokes=jokes, user=user)

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    page = request.form['redirpage']
    db().addJoke(text, userid())
    return redirect('/page/' + page)

@app.route('/edit', methods=['POST'])
def edit():
    text = request.form['text']
    page = request.form['redirpage']
    objectId = int(request.form['id'])
    if objectId in db().getUserJokes(userid()):
        db().updateJoke(text, objectId)
    return redirect('/page/' + page)

@app.route('/upvote', methods=['POST'])
def upvote():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    if objectId in db().getUserVotes(userid()):
        db().unvoteJoke(objectId, userid())
    db().voteJoke(objectId, False, userid())
    return redirect('/page/' + page)

@app.route('/downvote', methods=['POST'])
def downvote():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    if objectId in db().getUserVotes(userid()):
        db().unvoteJoke(objectId, userid())
    db().voteJoke(objectId, True, userid())
    return redirect('/page/' + page)

@app.route('/report', methods=['POST'])
def report():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    if objectId in db().getUserVotes(userid()):
        db().unvoteJoke(objectId, userid())
    db().reportJoke(objectId, userid())
    return redirect('/page/' + page)

@app.route('/delete', methods=['POST'])
def delete():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    if objectId in db().getUserJokes(userid()):
        db().removeJoke(objectId, userid())
    return redirect('/page/' + page)

@app.route('/undelete', methods=['POST'])
def undelete():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    if objectId in db().getUserJokes(userid()):
        db().unvoteJoke(objectId, userid())
    return redirect('/page/' + page)

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
    page = request.form['redirpage']
    return redirect('/page/' + page)

@app.route('/logout', methods=['POST'])
def logout():
    if 'userlogin' in session:
        del session['userlogin']
        flash('Ausgeloggt.')
    session['guestlogin'] = os.urandom(32).hex()
    page = request.form['redirpage']
    return redirect('/page/' + page)

@app.route('/static/<path:path>')
def get_static(path):
    return send_from_directory('static', path)

app.debug = config['debug']
app.secret_key = config['secret_key']
if __name__ == '__main__':
    app.run(host="0.0.0.0")

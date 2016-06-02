#!/usr/bin/python3
import markdown
from flask import (
    Flask,
    render_template,
    Markup,
    request,
    g,
    send_from_directory,
    redirect
)
import sqlite3


class dbProxy(object):
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = self.dict_factory
        self.c = self.conn.cursor()

    def create_v0(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, upvotes INTEGER, downvotes INTEGER, reports INTEGER)")
        self.c.execute("CREATE TABLE IF NOT EXISTS votes(id INTEGER PRIMARY KEY NOT NULL, ip TEXT, jokeid INTEGER, type INTEGER)")

    def create_v1(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS v1_jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE IF NOT EXISTS v1_users(id INTEGER PRIMARY KEY NOT NULL, identifier TEXT)")
        self.c.execute("CREATE TABLE IF NOT EXISTS v1_votes(id INTEGER PRIMARY KEY NOT NULL, joke INTEGER, user INTEGER, type TEXT)")

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
            'jokes': 0,
            'v1_jokes': 1
        }
        for ver in versions:
            if self.c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (ver,)).fetchone()['COUNT(*)'] == 1:
                return versions[ver]
        return -1

    def create(self):
        if self.database_v() == 0:
            print("migrating v0 to v1")
            self.migrate_v0to1()
        if self.database_v() == -1:
            print("creating new v1 db")
            self.create_v1()

    def close(self):
        self.conn.close()

    def dict_factory(self, cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d

    def sort(self, joke):
        #interactions = joke["reports"]*5 + joke["upvotes"] + joke["downvotes"] + 1
        #score = 1/interactions * (joke["upvotes"]+1)/interactions
        score = joke["upvotes"] - joke["downvotes"] - joke["reports"]*5
        return score

    def getPages(self, perpage):
        l = self.c.execute("SELECT COUNT(*) FROM v1_jokes").fetchone()['COUNT(*)']
        return int(l/perpage)+1

    def getJokes(self, perpage, page, ip):
        user = self.userByIp(ip)
        ret_jokes = []
        jokes = self.c.execute("SELECT * FROM v1_jokes").fetchall()
        for joke in jokes:
            ret_joke = {
                'id': joke['id'],
            }
            ret_joke['locked'] = not self.c.execute("SELECT COUNT(*) FROM v1_votes WHERE joke=? AND user=?", (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            ret_joke['mine'] = joke['user'] == user
            if joke['format'] == 'markdown':
                ret_joke['text'] = Markup(markdown.markdown(joke['text'], extensions=['markdown.extensions.nl2br'], output_format="html5", safe_mode="remove"))  # TODO safe_mode deprecated
            if joke['format'] == 'html':
                ret_joke['text'] = joke['text']

            typemap = (
                ("up", "upvotes"),
                ("down", "downvotes"),
                ("report", "reports")
            )
            for key, tag in typemap:
                ret_joke[tag] = self.c.execute("SELECT COUNT(*) FROM v1_votes WHERE joke=? AND type=?", (joke['id'], key)).fetchone()['COUNT(*)']

            ret_jokes.append(ret_joke)

        ret_jokes = sorted(ret_jokes, key=self.sort, reverse=True)
        return ret_jokes[page*perpage:(page+1)*perpage]

    def userByIp(self, ip):
        user = self.c.execute("SELECT * FROM v1_users WHERE identifier=?", (ip,)).fetchone()
        if user:
            return user['id']
        else:
            self.c.execute("INSERT INTO v1_users(identifier) VALUES(?)", (ip,))
            self.conn.commit()
            return self.c.lastrowid

    def addJoke(self, text, ip):
        user = self.userByIp(ip)
        self.c.execute("INSERT INTO v1_jokes(text, format, user) VALUES(?, 'markdown', ?)", (text, user))
        self.conn.commit()

    def voteJoke(self, objectId, down, ip):
        user = self.userByIp(ip)
        self.c.execute("INSERT INTO v1_votes(joke, user, type) VALUES(?, ?, ?)", (objectId, user, 'down' if down else 'up'))
        self.conn.commit()

    def reportJoke(self, objectId, ip):
        user = self.userByIp(ip)
        self.c.execute("INSERT INTO v1_votes(joke, user, type) VALUES(?, ?, 'report')", (objectId, user))
        self.conn.commit()

    def getUserVotes(self, ip):
        user = self.userByIp(ip)
        votes = self.c.execute("SELECT joke FROM v1_votes WHERE user=?", (user,)).fetchall()
        votes = [v['joke'] for v in votes]
        return votes


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

@app.route('/page/<int:num>')
def page(num):
    numpages = db().getPages(PERPAGE)
    ip = request.remote_addr
    jokes = db().getJokes(PERPAGE, num, ip)
    return render_template('index.html', currentpage=num, pages=[[]]*numpages, jokes=jokes)

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    page = request.form['redirpage']
    ip = request.remote_addr
    db().addJoke(text, ip)
    return redirect('/page/' + page)

@app.route('/vote', methods=['POST'])
def vote():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    ip = request.remote_addr
    if objectId not in db().getUserVotes(ip):
        db().voteJoke(objectId, request.form['vote'] == 'downvote', ip)
    return redirect('/page/' + page)

@app.route('/report', methods=['POST'])
def report():
    objectId = int(request.form['id'])
    page = request.form['redirpage']
    ip = request.remote_addr
    if objectId not in db().getUserVotes(ip):
        db().reportJoke(objectId, ip)
    return redirect('/page/' + page)

@app.route('/static/<path:path>')
def get_static(path):
    return send_from_directory('static', path)

app.debug = True
if __name__ == '__main__':
    app.run(host="0.0.0.0")

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

    def create(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, upvotes INTEGER, downvotes INTEGER, reports INTEGER)")
        self.c.execute("CREATE TABLE IF NOT EXISTS votes(id INTEGER PRIMARY KEY NOT NULL, ip TEXT, jokeid INTEGER, type INTEGER)")

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
        l = self.c.execute("SELECT COUNT(*) FROM jokes").fetchone()['COUNT(*)']
        return int(l/perpage)+1

    def getJokes(self, perpage, page):
        jokes = self.c.execute("SELECT * FROM jokes").fetchall()
        jokes = sorted(jokes, key=self.sort, reverse=True)
        return jokes[page*perpage:(page+1)*perpage]

    def addJoke(self, text):
        self.c.execute("INSERT INTO jokes(text, upvotes, downvotes, reports) VALUES (?, 0, 0, 0)", (text, ))
        self.conn.commit()

    def voteJoke(self, objectId, down, ip):
        if down:
            self.c.execute("UPDATE jokes SET downvotes=downvotes+1 WHERE id=?", (objectId,))
        else:
            self.c.execute("UPDATE jokes SET upvotes=upvotes+1 WHERE id=?", (objectId,))
        self.c.execute("INSERT INTO votes(ip, jokeid, type) VALUES (?, ?, ?)", (ip, objectId, -1 if down else +1))
        self.conn.commit()

    def reportJoke(self, objectId, ip):
        self.c.execute("UPDATE jokes SET reports=reports+1 WHERE id=?", (objectId,))
        self.c.execute("INSERT INTO votes(ip, jokeid, type) VALUES (?, ?, ?)", (ip, objectId, 0))
        self.conn.commit()

    def getUserVotes(self, ip):
        votes = self.c.execute("SELECT jokeid FROM votes WHERE ip=?", (ip, )).fetchall()
        votes = [v['jokeid'] for v in votes]
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
    jokes = db().getJokes(PERPAGE, num)
    ip = request.remote_addr
    votes = db().getUserVotes(ip)
    for joke in jokes:
        if joke['id'] in votes:
            joke['locked'] = True

    return render_template('index.html', currentpage=num, pages=[[]]*numpages, jokes=jokes)

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    html = Markup(markdown.markdown(text, extensions=['markdown.extensions.nl2br'], output_format="html5", safe_mode="remove"))  # TODO safe_mode deprecated
    page = request.form['redirpage']
    db().addJoke(html)
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

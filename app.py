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

    def close(self):
        self.conn.close()

    def dict_factory(self, cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d

    def sort(self, joke):
        interactions = joke["reports"]*5 + joke["upvotes"] + joke["downvotes"] + 1
        score = 1/interactions * (joke["upvotes"]+1)/interactions
        return score

    def getPages(self, perpage):
        l = self.c.execute("SELECT COUNT(*) FROM jokes").fetchone()['COUNT(*)']
        return int(l/perpage)+1

    def getJokes(self, perpage, page):
        jokes = self.c.execute("SELECT * FROM jokes LIMIT ? OFFSET ?", (perpage, perpage*page)).fetchall()
        jokes = sorted(jokes, key=self.sort, reverse=True)
        return jokes

    def addJoke(self, text):
        self.c.execute("INSERT INTO jokes(text, upvotes, downvotes, reports) VALUES (?, 0, 0, 0)", (text, ))
        self.conn.commit()

    def voteJoke(self, objectId, down):
        if down:
            self.c.execute("UPDATE jokes SET downvotes=downvotes+1 WHERE id=?", (objectId,))
        else:
            self.c.execute("UPDATE jokes SET upvotes=upvotes+1 WHERE id=?", (objectId,))
        self.conn.commit()

    def reportJoke(self, objectId):
        self.c.execute("UPDATE jokes SET reports=reports+1 WHERE id=?", (objectId,))
        self.conn.commit()


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
    return render_template('index.html', currentpage=num, pages=[[]]*numpages, jokes=db().getJokes(PERPAGE, num))

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    html = Markup(markdown.markdown(text, extensions=['markdown.extensions.nl2br'], output_format="html5", safe_mode="remove"))  # TODO safe_mode deprecated
    db().addJoke(html)
    return redirect('/')

@app.route('/vote', methods=['POST'])
def vote():
    objectId = request.form['id']
    db().voteJoke(objectId, request.form['vote'] == 'downvote')
    return redirect('/')

@app.route('/report', methods=['POST'])
def report():
    objectId = request.form['id']
    db().reportJoke(objectId)
    return redirect('/')

@app.route('/static/<path:path>')
def get_static(path):
    return send_from_directory('static', path)

app.debug = True
if __name__ == '__main__':
    app.run()

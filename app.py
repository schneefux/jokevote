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
        self.c = self.conn.cursor()

    def create(self):
        self.c.execute("CREATE TABLE IF NOT EXISTS jokes(id INTEGER PRIMARY KEY NOT NULL, text TEXT, upvotes INTEGER, downvotes INTEGER, reports INTEGER)")

    def close(self):
        self.conn.close()

    def getJokes(self):
        jokes = self.c.execute("SELECT * FROM jokes ORDER BY reports ASC").fetchall()
        jokes = [{"id": int(id), "text": text, "upvotes": int(upvotes), "downvotes": int(downvotes), "reports": int(reports)} for id, text, upvotes, downvotes, reports in jokes]  # TODO use factory
        return jokes

    def addJoke(self, text):
        self.c.execute("INSERT INTO jokes(text, upvotes, downvotes, reports) VALUES (?, 0, 0, 0)", (text, ))
        self.conn.commit()

    def voteJoke(self, objectId, down):
        if down:
            self.c.execute("UPDATE jokes SET downvotes=downvotes+1 WHERE id=?", (objectId))
        else:
            self.c.execute("UPDATE jokes SET upvotes=upvotes+1 WHERE id=?", (objectId))
        self.conn.commit()

    def reportJoke(self, objectId):
        self.c.execute("UPDATE jokes SET reports=reports+1 WHERE id=?", (objectId))
        self.conn.commit()


def db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = dbProxy("votes.db")
        db.create()
    return db

app = Flask(__name__)


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

@app.route('/')
def root():
    jokes = db().getJokes()
    for joke in jokes:
        joke['text'] = Markup(markdown.markdown(joke['text'], extensions=['markdown.extensions.nl2br'], output_format="html5", safe_mode="remove"))  # TODO cache TODO safe_mode deprecated
    return render_template('index.html', jokes=jokes)

@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    db().addJoke(text)  # TODO auth
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

# TODO sort by votes
# TODO buttons floating weird
if __name__ == '__main__':
    app.run(debug=True)

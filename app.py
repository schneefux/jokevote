from flask import (
    Flask,
    render_template,
    request
)
import pymongo


class dbProxy(object):
    def __init__(self, mongouri):
        self.client = pymongo.MongoClient(mongouri)
        self.db = self.client['jokevotedb']

    def getJokes(self):
        jokes = []
        for joke in self.db.jokes.find():
            jokes.append(joke)
        return jokes

    def addJoke(self, text):
        self.db.jokes.insert_one({'text': text, 'votes': 0})


app = Flask(__name__)
db = dbProxy(
    'mongodb://jokevote:jokevote@127.0.0.1:27017/jokevotedb')


@app.route('/')
def root():
    jokes = db.getJokes()
    return render_template('index.html', jokes=jokes)


@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    db.addJoke(text)  # TODO secure this
    return root()


if __name__ == '__main__':
    app.run(debug=True)

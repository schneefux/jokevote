#!/usr/bin/python3
# pylint: disable=missing-docstring,invalid-name
import sqlite3
import hashlib
import os
import re
import datetime
from flask import (
    Flask,
    render_template,
    make_response,
    request,
    abort,
    g,
    send_from_directory,
    redirect,
    session,
    flash
)
from config import config


class Markup(object):
    tagmark = "_"
    spacemark = " "

    def prettify_text(self, text):
        html = re.sub('<[^<]+?>', '', text)
        html = re.sub(r"/([\w ]+)/", '<em>\\1</em>', html)
        html = re.sub(r"\*([\w ]+)\*", '<strong>\\1</strong>', html)
        html = re.sub(r"#(\w+)", '<a href="/?filter=' +
                      self.tagmark + '\\1">#\\1</a>', html)
        html = html.replace("\n", "<br />")
        return html, text

    def clean_html(self, text):
        # allow "downgrading" html to text on next edit
        # by stripping html tags
        html = text
        text = re.sub('<[^<]+?>', '', text)
        return html, text


class DBSchemaHandler(object):
    def __init__(self, conn, c, prefix):
        self.conn = conn
        self.c = c
        self.prefix = prefix

        if self.database_v() == '-1':
            self.create_v1c()
        if self.database_v() == '0':
            self.migrate_v0to1()
        if self.database_v() == '1':
            self.migrate_v1to1a()
        if self.database_v() == '1a':
            self.migrate_v1ato1b()
        if self.database_v() == '1b':
            self.migrate_v1bto1c()

    def database_v(self):
        versions = {
            'jokes': '0',
            'v1_jokes': '1',
            'v1a_jokes': '1a',
            'v1b_jokes': '1b',
            'v1c_jokes': '1c'
        }
        for ver in versions:
            if self.c.execute("SELECT COUNT(*) FROM sqlite_master " +
                              "WHERE type='table' AND name=?",
                              (ver,)).fetchone()['COUNT(*)'] == 1:
                return versions[ver]
        return '-1'

    def create_v0(self):
        app.logger.warning("creating new v0 database")
        self.c.execute("CREATE TABLE jokes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "text TEXT, upvotes INTEGER, " +
                       "downvotes INTEGER, reports INTEGER)")
        self.c.execute("CREATE TABLE votes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "ip TEXT, jokeid INTEGER, type INTEGER)")
        self.conn.commit()

    def create_v1(self):
        app.logger.warning("creating new v1 database")
        self.c.execute("CREATE TABLE v1_jokes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE v1_users(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "identifier TEXT)")
        self.c.execute("CREATE TABLE v1_votes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "joke INTEGER, user INTEGER, type TEXT)")
        self.conn.commit()

    def create_v1a(self):
        app.logger.warning("creating new " + self.prefix + " database")
        self.c.execute("CREATE TABLE " + self.prefix + "_jokes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "text TEXT, format TEXT, user INTEGER)")
        self.c.execute("CREATE TABLE " + self.prefix + "_votes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "joke INTEGER, user INTEGER, type TEXT)")
        self.c.execute("CREATE TABLE " + self.prefix + "_users(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "identifier TEXT, role TEXT DEFAULT 'guest', " +
                       "password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.conn.commit()

    def create_v1b(self):
        self.create_v1a()

    def create_v1c(self):
        app.logger.warning("creating new " + self.prefix + " database")
        self.c.execute("CREATE TABLE " + self.prefix + "_jokes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "text TEXT, format TEXT, user INTEGER, " +
                       "created TIMESTAMP)")
        self.c.execute("CREATE TABLE " + self.prefix + "_votes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "joke INTEGER, user INTEGER, type TEXT)")
        self.c.execute("CREATE TABLE " + self.prefix + "_users(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "identifier TEXT, role TEXT DEFAULT 'guest', " +
                       "password TEXT DEFAULT '', salt TEXT DEFAULT '')")
        self.conn.commit()

    def migrate_v0to1(self):  # pylint: disable=too-many-locals
        app.logger.warning("migrating database from v0 to v1")
        self.create_v1()

        self.c.execute("INSERT INTO v1_users(identifier) VALUES ('anonymous')")
        anonymous = self.c.lastrowid

        # create new jokes
        j = self.c.execute("SELECT * FROM jokes").fetchall()
        j = [(int(n['id']), n['text'], anonymous) for n in j]
        self.c.executemany("INSERT INTO v1_jokes(id, text, format, user) " +
                           "VALUES(?, ?, 'html', ?)", j)

        votes = self.c.execute("SELECT * FROM votes").fetchall()

        # create new users
        ips = set([v['ip'] for v in votes])  # uniquify
        ips = [(ip,) for ip in ips]
        self.c.executemany("INSERT INTO v1_users(identifier) VALUES(?)", ips)

        # create votes
        for vote in votes:
            user = int(self.c.execute("SELECT id FROM v1_users " +
                                      "WHERE identifier=?",
                                      (vote['ip'],)).fetchone()['id'])
            if vote['type'] == -1:
                vtype = 'down'
            if vote['type'] == 0:
                vtype = 'report'
            if vote['type'] == 1:
                vtype = 'up'
            self.c.execute("INSERT INTO v1_votes(joke, user, type) " +
                           "VALUES(?, ?, ?)", (vote['jokeid'], user, vtype))

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
                legal_votes = int(self.c.execute("SELECT COUNT(*) FROM " +
                                                 "v1_votes WHERE id=? " +
                                                 "AND type=?", (joke, newtype))
                                  .fetchone()['COUNT(*)'])
                real_votes = int(self.c.execute("SELECT * FROM jokes " +
                                                "WHERE id=?",
                                                (joke,)).fetchone()[oldtype])
                diff = real_votes - legal_votes
                if diff:
                    self.c.executemany("INSERT INTO " +
                                       "v1_votes(joke, user, type) " +
                                       "VALUES(?, ?, ?)",
                                       [(joke, anonymous, newtype)]*diff)

        # drop old tables
        self.c.execute("DROP TABLE jokes")
        self.c.execute("DROP TABLE IF EXISTS votes")

        # write
        self.conn.commit()

    def migrate_v1to1a(self):
        app.logger.warning("migrating database from v1 to v1a")
        self.c.execute("ALTER TABLE v1_jokes RENAME TO v1a_jokes")
        self.c.execute("ALTER TABLE v1_votes RENAME TO v1a_votes")
        self.c.execute("CREATE TABLE v1a_users(" +
                       "id INTEGER PRIMARY KEY, identifier TEXT, " +
                       "role TEXT DEFAULT 'guest', " +
                       "password TEXT DEFAULT '', " +
                       "salt TEXT DEFAULT '')")
        self.c.execute("INSERT INTO v1a_users(id, identifier) SELECT id, " +
                       "identifier FROM v1_users")
        self.c.execute("DROP TABLE v1_users")
        self.conn.commit()

    def migrate_v1ato1b(self):
        app.logger.warning("migrating database from v1a to v1b")
        self.c.execute("ALTER TABLE v1a_jokes RENAME TO v1b_jokes")
        self.c.execute("ALTER TABLE v1a_votes RENAME TO v1b_votes")
        self.c.execute("ALTER TABLE v1a_users RENAME TO v1b_users")
        self.c.execute("UPDATE v1b_jokes SET format='prettytext' " +
                       "WHERE format='markdown'")
        self.c.execute("UPDATE v1b_votes SET type='down' WHERE type='report'")
        self.conn.commit()

    def migrate_v1bto1c(self):
        app.logger.warning("migrating database from v1b to v1c")
        app.logger.warning(
            "IMPORTANT! All usernames will be converted to lowercase." +
            "In case of duplicates, the oldest will be kept.")
        self.c.execute("CREATE TABLE v1c_jokes(" +
                       "id INTEGER PRIMARY KEY NOT NULL, " +
                       "text TEXT, format TEXT, user INTEGER, " +
                       "created TIMESTAMP)")
        self.c.execute("ALTER TABLE v1b_votes RENAME TO v1c_votes")
        self.c.execute("ALTER TABLE v1b_users RENAME TO v1c_users")
        # lowercase all usernames, throw away the most recent duplicate
        users = self.c.execute("SELECT id, identifier FROM v1c_users " +
                               "ORDER BY id").fetchall()
        dupes = []
        for cnt, user in enumerate(users):
            for ocnt, other in enumerate(users[cnt+1:]):
                if user["identifier"].lower() == other["identifier"].lower():
                    app.logger.warning("deleting duplicate user " +
                                       other["identifier"])
                    dupes.append((other["id"],))
                    del users[cnt+ocnt]

        self.c.executemany("UPDATE v1c_users SET " +
                           "role='guest', " +
                           "identifier=identifier||CAST(id AS TEXT), " +
                           "password='', salt='' WHERE id=?",
                           dupes)
        self.c.execute("UPDATE v1c_users SET " +
                       "identifier=LOWER(identifier)")

        # add a (fake) date
        jokes = self.c.execute("SELECT id, text, format, user " +
                               "FROM v1b_jokes").fetchall()
        newest_id = max([j['id'] for j in jokes])
        now = datetime.datetime.now()
        for joke in jokes:
            self.c.execute("INSERT INTO " +
                           "v1c_jokes(id, text, format, user, created) " +
                           "VALUES(?, ?, ?, ?, ?)",
                           (joke['id'],
                            joke['text'],
                            joke['format'],
                            joke['user'],
                            now-datetime.timedelta(
                                days=(newest_id-joke['id']))))
        self.c.execute("DROP TABLE v1b_jokes")
        self.conn.commit()


class DBProxy(object):
    def __init__(self, database, rootName):
        def dict_factory(cursor, row):
            dic = {}
            for idx, col in enumerate(cursor.description):
                dic[col[0]] = row[idx]
            return dic

        self.conn = sqlite3.connect(database,
                                    detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.row_factory = dict_factory
        self.c = self.conn.cursor()

        self.prefix = "v1c"
        DBSchemaHandler(self.conn, self.c, self.prefix)

        self.root_user(rootName)

    def close(self):
        self.conn.close()

    def score(self, jokeid):
        users = self.c.execute("SELECT id FROM " + self.prefix +
                               "_users WHERE role='user' " +
                               "OR role='super'").fetchall()
        users = [u['id'] for u in users]
        votewhere = "SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE "
        # TODO optimize queries
        score = 0
        score += self.c.execute(
            votewhere + "joke=? AND type='up'",
            (jokeid,)).fetchone()['COUNT(*)']
        score -= self.c.execute(
            votewhere + "joke=? AND type='down'",
            (jokeid,)).fetchone()['COUNT(*)']
        for user in users:
            # user's scores count 10 times more
            score += self.c.execute(
                votewhere + "joke=? AND type='up' AND user=?",
                (jokeid, user)).fetchone()['COUNT(*)'] * 9
            score -= self.c.execute(
                votewhere + "joke=? AND type='down' AND user=?",
                (jokeid, user)).fetchone()['COUNT(*)'] * 9
        return score

    def get_jokes(self, user=None, search=None, sortby='rank'):
        # user: return with user-specific attributes, also return deleted jokes
        # search: return jokes including all the specified words
        # sortby:
        #   rank - calculated by score and freshness,
        #   score - only score
        #   unread - without interaction first, then freshness
        #   age - freshness
        ret_jokes = []
        jokes = self.c.execute("SELECT * FROM " + self.prefix +
                               "_jokes ORDER BY id ASC").fetchall()
        votewhere = "SELECT COUNT(*) FROM " + self.prefix + "_votes WHERE "
        iamroot = not self.c.execute("SELECT COUNT(*) FROM " + self.prefix +
                                     "_users WHERE id=? AND role='super'",
                                     (user,)).fetchone()['COUNT(*)'] == 0
        now = datetime.datetime.now()
        for joke in jokes:
            ret_joke = {
                'id': joke['id']
            }
            # mark jokes the user has already interacted with
            ret_joke['upvoted'] = not self.c.execute(
                votewhere + "joke=? AND user=? AND type='up'",
                (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            ret_joke['downvoted'] = not self.c.execute(
                votewhere + "joke=? AND user=? AND type='down'",
                (joke['id'], user)).fetchone()['COUNT(*)'] == 0
            # allow deletion
            ret_joke['mine'] = (joke['user'] == user or iamroot)

            if joke['format'] == 'prettytext':
                ret_joke['html'], ret_joke['text'] = Markup().prettify_text(
                    joke['text'])
            if joke['format'] == 'html':
                ret_joke['html'], ret_joke['text'] = Markup().clean_html(
                    joke['text'])

            if search is not None:  # simple search
                match = True
                for word in search:
                    if word.lower() not in ret_joke['text'].lower():
                        match = False
                        break
                if not match:
                    continue

            ret_joke['score'] = self.score(joke['id'])
            ret_joke['freshness'] = (now - joke['created']).days

            # skip other's jokes marked as deleted
            if not self.c.execute("SELECT COUNT(*) FROM " + self.prefix +
                                  "_votes WHERE type='delete' AND joke=?",
                                  (joke['id'],)).fetchone()['COUNT(*)'] == 0:
                # deleted
                if ret_joke['mine']:
                    ret_joke['deleted'] = True
                    ret_joke['score'] = -100
                else:
                    continue

            ret_jokes.append(ret_joke)

        def sorter(j):
            if sortby == 'unread':
                return -j['freshness'] \
                    if j['upvoted'] or j['downvoted'] \
                    else j['freshness']
            if sortby == 'age':
                return 1-j['freshness']
            if sortby == 'score':
                return j['score']
            # 'rank' or invalid
            return (j['score']+1)/pow(j['freshness']+1, 1.8)

        ret_jokes = sorted(ret_jokes, key=sorter, reverse=True)
        return ret_jokes

    def add_user(self, name, password):
        # allow words combined by '.', '-', ' '
        if re.match(r"^\w+(([. -])?\w+)*$", name) is None or len(name) < 3:
            return -3  # invalid username
        if self.get_user(name=name) != -2:
            return -1  # already registered
        password = password.encode('utf-8')
        salt = os.urandom(32).hex().encode('utf-8')
        password = hashlib.sha512(password + salt).hexdigest()
        self.c.execute("INSERT INTO " + self.prefix +
                       "_users(identifier, password, salt, role) " +
                       "VALUES(?, ?, ?, 'user')", (name, password, salt))
        self.conn.commit()
        return self.c.lastrowid

    def get_user(self, cookie=None, name=None, password=None):
        # cookie != None: return or create guest uid
        # name != None: return user uid or -2 (not found)
        # name, password != None: uid, -2 (not found) or -1 (wrong password)
        if cookie is not None:
            user = self.c.execute("SELECT id FROM " + self.prefix +
                                  "_users WHERE role='guest' AND identifier=?",
                                  (cookie,)).fetchone()
            if user:  # existing guest
                uid = user['id']
            else:  # create guest
                self.c.execute("INSERT INTO " + self.prefix +
                               "_users(identifier) VALUES(?)",
                               (cookie,))
                self.conn.commit()
                uid = self.c.lastrowid
        else:
            if name is not None:
                # validate username, see add_user
                if re.match(r"^\w+(([. -])?\w+)*$", name) is None or len(name) < 3:
                    return -3  # invalid username

                user = self.c.execute("SELECT id FROM " + self.prefix +
                                      "_users WHERE identifier=?",
                                      (name,)).fetchone()
                if user:  # existing user
                    uid = user['id']
                    if password is not None:
                        password = password.encode('utf-8')
                        auth = self.c.execute(
                            "SELECT password, salt FROM " + self.prefix +
                            "_users WHERE id=?", (uid,)).fetchone()
                        calc = hashlib.sha512(password + auth['salt'])
                        if calc.hexdigest() != auth['password']:
                            uid = -1  # auth failed
                else:
                    uid = -2  # nonexistent

        return uid

    def root_user(self, name):
        app.logger.warning("converting %s to superuser", name)
        self.c.execute("UPDATE " + self.prefix + "_users SET role='super' " +
                       "WHERE identifier=?", (name,))
        self.conn.commit()

    def add_joke(self, text, user):
        self.c.execute(
            "INSERT INTO " + self.prefix + "_jokes" +
            "(text, format, user, created) " +
            "VALUES(?, 'prettytext', ?, ?)",
            (text, user, datetime.datetime.now()))
        self.conn.commit()

    def update_joke(self, text, joke):
        self.c.execute("UPDATE " + self.prefix + "_jokes " +
                       "SET text=?, format='prettytext' WHERE id=?",
                       (text, joke))
        self.conn.commit()

    def remove_joke(self, joke, user):
        self.c.execute(
            "INSERT INTO " + self.prefix + "_votes(joke, user, type) " +
            "VALUES(?, ?, 'delete')", (joke, user))
        self.conn.commit()

    def vote_joke(self, joke, down, user):
        self.c.execute(
            "INSERT INTO " + self.prefix + "_votes(joke, user, type) " +
            "VALUES(?, ?, ?)", (joke, user, 'down' if down else 'up'))
        self.conn.commit()

    def unvote_joke(self, joke, user):
        self.c.execute("DELETE FROM " + self.prefix + "_votes " +
                       "WHERE joke=? AND user=?", (joke, user))
        self.conn.commit()

    def has_voted(self, joke, user):
        return not self.c.execute("SELECT COUNT(*) FROM " + self.prefix +
                                  "_votes WHERE joke=? AND user=?",
                                  (joke, user)).fetchone()['COUNT(*)'] == 0

    def may_modify_joke(self, joke, user):
        if self.c.execute("SELECT role FROM " + self.prefix +
                          "_users WHERE id=?",
                          (user,)).fetchone()['role'] == 'super':
            return True
        if self.c.execute("SELECT user FROM " + self.prefix +
                          "_jokes WHERE id=?",
                          (joke,)).fetchone()['user'] == user:
            return True
        return False


def db():
    database = getattr(g, "_database", None)
    if database is None:
        database = g._database = DBProxy("votes.db",
                                         config['superuser'].lower())
    return database

app = Flask(__name__)


@app.teardown_appcontext
def close_db(exception):  # pylint: disable=unused-argument
    database = getattr(g, "_database", None)
    if database is not None:
        database.close()


def userid():
    if 'userlogin' in session:
        uid = db().get_user(name=session['userlogin'].lower())
        if uid != -2:
            return uid
    if 'guestlogin' not in session:
        session['guestlogin'] = os.urandom(32).hex()
    return db().get_user(cookie=session['guestlogin'])


@app.route('/')
def root():
    return page(0)


@app.route('/page/<int:num>')
def page(num):
    search = request.args.get('filter')
    sortmethod = str(request.args.get('sort')) or 'rank'
    if search:
        # TODO find a cleaner way
        # visual: #, actual: _
        search = re.sub(r"^" + Markup.tagmark, "#", search)
        search = search.split(Markup.spacemark)
    perpage = abs(int(request.args.get('perpage') or 10))
    jokes = db().get_jokes(user=userid(), search=search, sortby=sortmethod)
    user = {'loggedin': False}
    if 'userlogin' in session:
        user['loggedin'] = True
        user['name'] = session['userlogin']
    resp = make_response(
        render_template(
            'index.html',
            currentpage=num,
            tags=search,
            perpage=perpage,
            jokes=jokes,
            abusemail=config['abusemail'],
            title=config['title'],
            featured=config['featured'],
            user=user))
    resp.headers.set('X-SmoothState-Location',
                     request.path + "?" +
                     str(request.query_string, "utf-8"))
    return resp


@app.route('/submit', methods=['POST'])
def submit():
    text = request.form['text']
    db().add_joke(text, userid())
    return redirect(request.referrer)


@app.route('/edit', methods=['POST'])
def edit():
    text = request.form['text']
    joke = int(request.form['id'])
    if not db().may_modify_joke(joke, userid()):
        abort(403)
    db().update_joke(text, joke)
    return redirect(request.referrer)


@app.route('/upvote', methods=['POST'])
def upvote():
    joke = int(request.form['id'])
    if db().has_voted(joke, userid()):
        db().unvote_joke(joke, userid())
    db().vote_joke(joke, False, userid())
    return redirect(request.referrer)


@app.route('/downvote', methods=['POST'])
def downvote():
    joke = int(request.form['id'])
    if db().has_voted(joke, userid()):
        db().unvote_joke(joke, userid())
    db().vote_joke(joke, True, userid())
    return redirect(request.referrer)


@app.route('/delete', methods=['POST'])
def delete():
    joke = int(request.form['id'])
    if not db().may_modify_joke(joke, userid()):
        abort(403)
    db().remove_joke(joke, userid())
    return redirect(request.referrer)


@app.route('/undelete', methods=['POST'])
def undelete():
    joke = int(request.form['id'])
    if not db().may_modify_joke(joke, userid()):
        abort(403)
    db().unvote_joke(joke, userid())
    return redirect(request.referrer)


@app.route('/login', methods=['POST'])
def login():
    if 'guestlogin' not in session:
        session['guestlogin'] = os.urandom(32).hex()
    name = request.form['user'].lower()
    passw = request.form['password']
    if db().add_user(name, passw) >= 0:
        flash('Erfolgreich registriert.')

    res = db().get_user(name=name, password=passw)
    if res == -3:
        flash('Benutzername ungültig.')
    if res == -2:
        flash('Benutzer existiert nicht.')
    if res == -1:
        flash('Passwort falsch.')
    if res >= 0:
        session['userlogin'] = name
        flash('Erfolgreich eingeloggt.')
    return redirect(request.referrer)


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    if 'userlogin' in session:
        del session['userlogin']
        flash('Ausgeloggt.')
    session['guestlogin'] = os.urandom(32).hex()
    return redirect(request.referrer)


@app.route('/export')
def export():
    search = request.args.get('filter')
    jokes = db().get_jokes(search=search, sortby='score')
    texts = [j['text'] for j in jokes]
    res = make_response("\n\r\n\r".join(texts))
    res.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return res


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

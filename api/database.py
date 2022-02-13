from datetime import timedelta

from flask_sqlalchemy import SQLAlchemy
from flask import Flask
from flask_migrate import Migrate
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker

app = Flask(__name__)
app.config['DEBUG'] = False
app.config['JWT_SECRET_KEY'] = 'owcBrtneZ-AgIfGFS3Wel8KXQUjJDr7mA1grv1u7Ra0'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:postgres@localhost:5432/balance-bot'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app=app, session_options={'autoflush': False})
migrate = Migrate(app, db)

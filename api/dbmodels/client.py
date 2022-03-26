from typing import List

import discord
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy_utils.types.encrypted.encrypted_type import StringEncryptedType, FernetEngine

from api.database import db
from api.dbmodels.serializer import Serializer
import os
import dotenv
import config

from api.dbmodels import balance

dotenv.load_dotenv()

_key = os.environ.get('ENCRYPTION_SECRET')
assert _key, 'Missing ENCRYPTION_SECRET in env'


class Client(db.Model, Serializer):
    __tablename__ = 'client'
    __serializer_forbidden__ = ['api_secret']

    # Identification
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=True)
    discord_user_id = db.Column(db.Integer, db.ForeignKey('discorduser.id', ondelete="CASCADE"), nullable=True)

    # User Information
    api_key = db.Column(db.String(), nullable=False)
    #api_secret = db.Column(db.String(), nullable=False)
    api_secret = db.Column(StringEncryptedType(db.String(), _key.encode('utf-8'), FernetEngine), nullable=False)
    exchange = db.Column(db.String, nullable=False)
    subaccount = db.Column(db.String, nullable=True)
    extra_kwargs = db.Column(db.PickleType, nullable=True)

    # Data
    name = db.Column(db.String, nullable=True)
    rekt_on = db.Column(db.DateTime, nullable=True)
    trades = db.relationship('Trade', backref='client', lazy=True, cascade="all, delete")
    history = db.relationship('Balance', backref='client',
                              cascade="all, delete", lazy=True, order_by='Balance.time')

    required_extra_args: List[str] = []

    @hybrid_property
    def latest(self):
        try:
            return self.history[len(self.history) - 1]
        except ValueError:
            return None

    @hybrid_property
    def is_global(self):
        return self.discorduser.global_client_id == self.id if self.discorduser else False

    @hybrid_property
    def is_active(self):
        return not all(not event.is_active for event in self.events)

    @hybrid_property
    def initial(self):
        try:
            return self.history[0]
        except ValueError:
            return balance.Balance(amount=config.REGISTRATION_MINIMUM, currency='$', error=None, extra_kwargs={})

    def get_event_string(self, is_global=False):
        events = ''
        if self.is_global or is_global:
            events += 'Global'
        for event in self.events:
            first = True
            if event.is_active or event.is_free_for_registration:
                if not first or self.is_global or is_global:
                    events += f', '
                events += event.name
                first = False
        return events

    def get_discord_embed(self, is_global=False):

        embed = discord.Embed(title="User Information")
        embed.add_field(name='Event', value=self.get_event_string(is_global), inline=False)
        embed.add_field(name='Exchange', value=self.exchange)
        embed.add_field(name='Api Key', value=self.api_key)

        if self.subaccount:
            embed.add_field(name='Subaccount', value=self.subaccount)
        for extra in self.extra_kwargs:
            embed.add_field(name=extra, value=self.extra_kwargs[extra])

        if len(self.history) > 0:
            embed.add_field(name='Initial Balance', value=self.history[0].to_string())

        return embed

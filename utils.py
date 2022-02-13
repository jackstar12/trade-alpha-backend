import re
import logging
from functools import wraps

import discord

from api.dbmodels.client import Client
from api.dbmodels.discorduser import DiscordUser
from errors import UserInputError
from discord_slash.utils.manage_components import create_button, create_actionrow
from discord_slash.model import ButtonStyle
from discord_slash import SlashCommand, ComponentContext, SlashContext
from usermanager import UserManager
from datetime import datetime, timedelta
from discord_slash import SlashContext, SlashCommandOptionType
from discord_slash.model import BaseCommandObject
from discord_slash.utils.manage_commands import create_choice, create_option
from typing import List, Tuple, Callable, Optional, Union
from api.dbmodels.balance import Balance
from models.discorduser import DiscordUser
from config import CURRENCY_PRECISION


def dm_only(coro):
    @wraps(coro)
    async def wrapper(ctx, *args, **kwargs):
        if ctx.guild:
            await ctx.send('This command can only be used via a Private Message.')
            return
        return await coro(ctx, *args, **kwargs)

    return wrapper


def admin_only(coro):
    @wraps(coro)
    async def wrapper(ctx: SlashContext, *args, **kwargs):
        if ctx.author.guild_permissions.administrator:
            return await coro(ctx, *args, **kwargs)
        else:
            await ctx.send('This command can only be used by administrators', hidden=True)

    return wrapper


def server_only(coro):
    @wraps(coro)
    async def wrapper(ctx: SlashContext, *args, **kwargs):
        if not ctx.guild:
            await ctx.send('This command can only be used in a server.')
            return
        return await coro(ctx, *args, **kwargs)

    return wrapper


def set_author_default(name: str):
    def decorator(coro):
        @wraps(coro)
        async def wrapper(ctx: SlashContext, *args, **kwargs):
            user = kwargs.get(name)
            if user is None:
                kwargs[name] = ctx.author
            return await coro(ctx, *args, **kwargs)
        return wrapper
    return decorator


def time_args(names: List[Tuple[str, Optional[str]]], allow_future=False):
    """
    Handy decorator for using time arguments.
    After applying this decorator you also have to apply log_and_catch_user_input_errors
    :param names: Tuple for each time argument: (argument name, default value)
    :param allow_future: whether dates in the future are permitted
    :return:
    """
    def decorator(coro):
        @wraps(coro)
        async def wrapper(*args, **kwargs):
            for name, default in names:
                time_arg = kwargs.get(name)
                if not time_arg:
                    time_arg = default
                if time_arg:
                    time = calc_time_from_time_args(time_arg, allow_future)
                    kwargs[name] = time
            return await coro(*args, **kwargs)
        return wrapper
    return decorator


def log_and_catch_user_input_errors(log_args=True):
    def decorator(coro):
        @wraps(coro)
        async def wrapper(ctx: SlashContext, *args, **kwargs):
            logging.info(f'New Interaction: '
                         f'Execute command {coro.__name__}, requested by {de_emojify(ctx.author.display_name)} '
                         f'guild={ctx.guild} {f"{args=}, {kwargs=}" if log_args else ""}')
            try:
                await coro(ctx, *args, **kwargs)
                logging.info(f'Done executing command {coro.__name__}')
            except UserInputError as e:
                if e.user_id:
                    e.reason = e.reason.replace('{name}', ctx.guild.get_member(e.user_id).display_name)
                await ctx.send(e.reason, hidden=True)
                logging.error(f'Failed because of UserInputError: {de_emojify(e.reason)}')
        return wrapper
    return decorator


# Thanks Stackoverflow
def de_emojify(text):
    regrex_pattern = re.compile("["
                                u"\U0001F600-\U0001F64F"  # emoticons
                                u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                                u"\U0001F680-\U0001F6FF"  # transport & map symbols
                                u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                                u"\U00002500-\U00002BEF"  # chinese char
                                u"\U00002702-\U000027B0"
                                u"\U00002702-\U000027B0"
                                u"\U000024C2-\U0001F251"
                                u"\U0001f926-\U0001f937"
                                u"\U00010000-\U0010ffff"
                                u"\u2640-\u2642"
                                u"\u2600-\u2B55"
                                u"\u200d"
                                u"\u23cf"
                                u"\u23e9"
                                u"\u231a"
                                u"\ufe0f"  # dingbats
                                u"\u3030"
                                "]+", re.UNICODE)
    return regrex_pattern.sub(r'', text)


def get_user_by_id(users_by_id,
                   user_id: int,
                   guild_id: int = None,
                   exact: bool = False,
                   throw_exceptions=True) -> DiscordUser:
    """
    Tries to find a matching entry for the user and guild id.
    :param exact: whether the global entry should be used if the guild isn't registered
    :return:
    The found user. It will never return None if throw_exceptions is True, since an ValueError exception will be thrown instead.
    """
    result = None

    if user_id in users_by_id:
        endpoints = users_by_id[user_id]
        if isinstance(endpoints, dict):
            result = endpoints.get(guild_id, None)
            if not result and not exact:
                result = endpoints.get(None, None)  # None entry is global
            if not result and throw_exceptions:
                raise ValueError("User {name} not registered for this guild")
        else:
            logging.error(
                f'users_by_id contains invalid entry! Associated data with {user_id=}: {result=} {endpoints=} ({guild_id=})')
            if throw_exceptions:
                raise ValueError("This is caused due to a bug in the bot. Please contact dev.")
    elif throw_exceptions:
        logging.error(f'Dont know user {user_id=}')
        raise ValueError("Unknown user {name}. Please register first.")

    return result


def add_guild_option(guilds, command: BaseCommandObject, description: str):
    command.options.append(
        create_option(
            name="guild",
            description=description,
            required=False,
            option_type=SlashCommandOptionType.STRING,
            choices=[
                create_choice(
                    name=guild.name,
                    value=str(guild.id)
                ) for guild in guilds
            ]
        )
    )


def calc_percentage(then: float, now: float, string=True) -> Union[str, float]:
    diff = now - then
    if diff == 0.0:
        result = '0' if string else 0.0
    elif then > 0:
        result = f'{round(100 * (diff / then), ndigits=3)}' if string else round(100 * (diff / then), ndigits=3)
    else:
        result = 'nan' if string else 0.0
    return result


def create_leaderboard(guild: discord.Guild, mode: str, time: datetime = None):
    user_scores: List[Tuple[DiscordUser, float]] = []
    value_strings: Dict[DiscordUser, str] = {}
    users_rekt: List[DiscordUser] = []
    clients_missing: List[DiscordUser] = []

    footer = ''
    description = ''

    event = dbutils.get_event(guild.id, throw_exceptions=False)

    if event:
        clients = event.registrations
    else:
        clients = []
        # All global clients
        users = DiscordUser.query.filter(DiscordUser.global_client_id).all()
        for user in users:
            member = guild.get_member(user.user_id)
            if member:
                clients.append(user.global_client)

    user_manager.fetch_data(clients=clients)

    if mode == 'balance':
        for client in clients:
            if client.rekt_on:
                users_rekt.append(client)
            elif len(client.history) > 0:
                balance = client.history[len(client.history) - 1]
                if balance.amount > REKT_THRESHOLD:
                    user_scores.append((client, balance.amount))
                    value_strings[client] = balance.to_string(display_extras=False)
                else:
                    users_rekt.append(client)
            else:
                clients_missing.append(client)
    elif mode == 'gain':

        description += f'Gain {utils.readable_time(time)}\n\n'

        client_gains = calc_gains(clients, guild.id, time)

        for client, client_gain in client_gains:
            if client_gain is not None:
                if client.rekt_on:
                    users_rekt.append(client)
                else:
                    user_gain_rel, user_gain_abs = client_gain
                    user_scores.append((client, user_gain_rel))
                    value_strings[client] = f'{user_gain_rel}% ({user_gain_abs}$)'
            else:
                clients_missing.append(client)
    else:
        raise UserInputError(f'Unknown mode {mode} was passed in')

    user_scores.sort(key=lambda x: x[1], reverse=True)
    rank = 1
    rank_true = 1

    if len(user_scores) > 0:
        prev_score = None
        for client, score in user_scores:
            member = guild.get_member(client.discorduser.user_id)
            if member:
                if prev_score is not None and score < prev_score:
                    rank = rank_true
                if client in value_strings:
                    value = value_strings[client]
                    description += f'{rank}. **{member.display_name}** {value}\n'
                    rank_true += 1
                else:
                    logger.error(f'Missing value string for {client=} even though hes in user_scores')
                prev_score = score

    if len(users_rekt) > 0:
        description += f'\n**Rekt**\n'
        for user_rekt in users_rekt:
            member = guild.get_member(user_rekt.discorduser.user_id)
            if member:
                description += f'{member.display_name}'
                if user_rekt.rekt_on:
                    description += f' since {user_rekt.rekt_on.replace(microsecond=0)}'
                description += '\n'

    if len(clients_missing) > 0:
        description += f'\n**Missing**\n'
        for client_missing in clients_missing:
            member = guild.get_member(client_missing.discorduser.user_id)
            if member:
                description += f'{member.display_name}\n'

    description += f'\n{footer}'

    logging.info(f"Done creating leaderboard.\nDescription:\n{de_emojify(description)}")
    return discord.Embed(
        title='Leaderboard :medal:',
        description=description
    )


def calc_gains(clients: List[Client],
               guild_id: int,
               search: datetime,
               currency: str = None) -> List[Tuple[Client, Tuple[float, float]]]:
    """
    :param guild_id:
    :param clients: users to calculate gain for
    :param search: date since when gain should be calculated
    :param currency:
    :param since_start: should the gain since the start be calculated?
    :return:
    Gain for each user is stored in a list of tuples following this structure: (User, (user gain rel, user gain abs)) success
                                                                               (User, None) missing
    """

    if currency is None:
        currency = '$'

    results = []
    user_manager = UserManager()
    for client in clients:
        data = user_manager.get_client_history(client, guild_id, start=search)
        if len(data) > 0:
            balance_then = user_manager.db_match_balance_currency(data[0], currency)
            balance_now = user_manager.db_match_balance_currency(data[len(data) - 1], currency)
            diff = round(balance_now.amount - balance_then.amount,
                         ndigits=CURRENCY_PRECISION.get(currency, 3))
            if balance_then.amount > 0:
                results.append((client,
                                (round(100 * (diff / balance_then.amount),
                                       ndigits=CURRENCY_PRECISION.get('%', 2)), diff)))
            else:
                results.append((client, (0.0, diff)))
        else:
            results.append((client, None))

    return results


def calc_time_from_time_args(time_str: str, allow_future=False) -> datetime:
    """
    Calculates time from given time args.
    Arg Format:
      <n><f>
      where <f> can be m (minutes), h (hours), d (days) or w (weeks)

      or valid time string

    :raise:
      ValueError if invalid arg is given
    :return:
      Calculated timedelta or None if None was passed in
    """

    if not time_str:
        return None

    time_str = time_str.lower()

    # Different time formats: True or False indicates whether the date is included.
    formats = [
        (False, "%H:%M:%S"),
        (False, "%H:%M"),
        (False, "%H"),
        (True, "%d.%m.%Y %H:%M:%S"),
        (True, "%d.%m.%Y %H:%M"),
        (True, "%d.%m.%Y %H"),
        (True, "%d.%m.%Y"),
        (True, "%d.%m. %H:%M:%S"),
        (True, "%d.%m. %H:%M"),
        (True, "%d.%m. %H"),
        (True, "%d.%m.")
    ]

    date = None
    now = datetime.now()
    for includes_date, time_format in formats:
        try:
            date = datetime.strptime(time_str, time_format)
            if not includes_date:
                date = date.replace(year=now.year, month=now.month, day=now.day, microsecond=0)
            elif date.year == 1900:  # %d.%m. not setting year to 1970 but to 1900?
                date = date.replace(year=now.year)
            break
        except ValueError:
            continue

    if not date:
        minute = 0
        hour = 0
        day = 0
        week = 0
        args = time_str.split(' ')
        if len(args) > 0:
            for arg in args:
                try:
                    if 'h' in arg:
                        hour += int(arg.rstrip('h'))
                    elif 'm' in arg:
                        minute += int(arg.rstrip('m'))
                    elif 'w' in arg:
                        week += int(arg.rstrip('w'))
                    elif 'd' in arg:
                        day += int(arg.rstrip('d'))
                    else:
                        raise UserInputError(f'Invalid time argument: {arg}')
                except ValueError:  # Make sure both cases are treated the same
                    raise UserInputError(f'Invalid time argument: {arg}')
        date = now - timedelta(hours=hour, minutes=minute, days=day, weeks=week)

    if not date:
        raise UserInputError(f'Invalid time argument: {time_str}')
    elif date > now and not allow_future:
        raise UserInputError(f'Future dates are not allowed. {time_str}')

    return date


def calc_xs_ys(data: List[Balance],
               percentage=False) -> Tuple[List[datetime], List[float]]:
    xs = []
    ys = []
    for balance in data:
        xs.append(balance.time.replace(microsecond=0))
        if percentage:
            if data[0].amount > 0:
                amount = 100 * (balance.amount - data[0][1].amount) / data[0][1].amount
            else:
                amount = 0.0
        else:
            amount = balance.amount
        ys.append(round(amount, ndigits=CURRENCY_PRECISION.get(balance.currency, 3)))
    return xs, ys


def create_yes_no_button_row(slash: SlashCommand,
                             author_id: int,
                             yes_callback: Callable = None,
                             no_callback: Callable = None,
                             yes_message: str = None,
                             no_message: str = None,
                             hidden=False):
    """

    Utility method for creating a yes/no interaction
    Takes in needed parameters and returns the created buttons as an ActionRow which are wired up to the callbacks.
    These must be added to the message.

    :param slash: Slash Command Handler to use
    :param author_id: Who are the buttons correspond to?
    :param yes_callback: Optional callback for yes button
    :param no_callback: Optional callback no button
    :param yes_message: Optional message to print on yes button
    :param no_message: Optional message to print on no button
    :param hidden: whether the response message should be hidden or not
    :return: ActionRow containing the buttons.
    """
    yes_id = f'yes_button_{author_id}'
    no_id = f'no_button_{author_id}'

    buttons = [
        create_button(
            style=ButtonStyle.green,
            label='Yes',
            custom_id=yes_id
        ),
        create_button(
            style=ButtonStyle.red,
            label='No',
            custom_id=no_id
        )
    ]

    def wrap_callback(custom_id: str, callback=None, message=None):
        if slash.get_component_callback(custom_id=custom_id) is not None:
            slash.remove_component_callback(custom_id=custom_id)

        @slash.component_callback(components=[custom_id])
        async def wrapper(ctx: ComponentContext):

            if callable(callback):
                callback()
            await ctx.edit_origin(components=[])
            if message:
                await ctx.send(content=message, hidden=hidden)
            for button in buttons:
                slash.remove_component_callback(custom_id=button['custom_id'])

    wrap_callback(yes_id, yes_callback, yes_message)
    wrap_callback(no_id, no_callback, no_message)

    return create_actionrow(*buttons)


def create_event_selection(custom_id: str, callback: Callable[[str], None] = None, message=None):
    pass


def readable_time(time: datetime):
    now = datetime.now()
    if time is None:
        time_str = 'since start'
    else:
        if time.date() == now.date():
            time_str = f'since {time.strftime("%H:%M")}'
        elif time.year == now.year:
            time_str = f'since {time.strftime("%d.%m. %H:%M")}'
        else:
            time_str = f'since {time.strftime("%d.%m.%Y %H:%M")}'

    return time_str

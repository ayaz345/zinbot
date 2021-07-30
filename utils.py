"""Utility functions for various tasks of 'zinbot."""
import json
from dataclasses import dataclass
from json.decoder import JSONDecodeError
from typing import Any, Callable, Literal

from pywikibot import Page, Site
from requests import Response
from requests_oauthlib import OAuth1Session

from config import bot_auth

API_URL = "https://test.wikipedia.org/w/api.php?"
WIKI_URL = "https://test.wikipedia.org/wiki/"


class APIError(Exception):
    """Exception raised by issues in dealing with the MediaWiki API."""

    def __init__(self, msg: str, event: Any = None) -> None:
        """Saves MW API error content, if any is passed.

        Saves to logs/APIError.json is JSON-serializable,
        logs/APIError.txt otherwise.

        Args:
          msg:  A str to pass as Exception's arg.
          event:  The error content from the MW API, in JSON-
            serializable format if possible.
        """
        super().__init__(msg)
        if event:
            try:
                with open("logs/APIError.json", 'w') as f:
                    json.dump(event, f)
            except TypeError:
                with open("logs/APIError.txt", 'w') as f:
                    f.write(str(event))


class ZBError(Exception):
    """Generic exception for errors specific to 'zinbot's behavior."""


@dataclass
class Authorization:
    """Dataclass for OAuth1Token.  Call privately in config.

    Args / Attributes:
      Match the output of the MW OAuth consumer dialog.
    """
    client_key: str
    client_secret: str
    access_key: str
    access_secret: str

    def __repr__(self) -> str:
        return(f"Authorization({self.client_key}, {self.client_secret}, "
               f"{self.access_key}, {self.access_secret})")

    def __str__(self) -> str:
        return(f"<Authorization object with access key {self.access_key}>")

    def session(self) -> OAuth1Session:
        """Create OAuth1Session using instance data attributes."""
        return OAuth1Session(self.client_key,
                             client_secret=self.client_secret,
                             resource_owner_key=self.access_key,
                             resource_owner_secret=self.access_secret)


class Bot:
    """Contains global information for the bot.

    Class attribute:
      TokenType:  A type alias for the valid values of `token` in an MW
        API request.

    Instance attributes:
      session:  An OAuth1Session that signs all API requests with the
        bot's OAuth data.
      site:  The value of pywikibot.Site()
    """
    TokenType = Literal['createaccount', 'csrf', 'deleteglobalaccount',
                        'login', 'patrol', 'rollback',
                        'setglobalaccountstatus', 'userrights', 'watch']

    def __init__(self, auth: Authorization) -> None:
        """Initializes Bot object.

        Args:
          auth:  An Authorization with which to configure an
            OAuth1Session.
        """
        self._auth = auth
        self.session = auth.session()
        # Yes it caches, but still preferable to only call once.
        self.site = Site()

    def __repr__(self) -> str:
        return f"Bot({self._auth!r})"

    def __str__(self) -> str:
        return f"Bot({self._auth})"

    # Could do a whole type implementation of the API's response as
    # dict[str, Any] or a list thereof, but given that `requests`
    # doesn't see that as necessary, neither do I.  Instead, type return
    # of functions that implement.
    def _api(self, methodname: Literal['get', 'post'],
             **kwargs: dict[str, str]) -> Any:
        """Error handling and JSON conversion for API functions.

        Args:
          method:  A str matching the name of a method that an
            OAuth1Session can have.
          **kwargs:  Keyword arguments to pass to `method`.

        Returns:
          An object matching the JSON structure of the relevant API
          Response.

        Raises:
          requests.HTTPError:  Issue connecting with API.
          APIError from JSONDecodeError:  API Response output was not
            decodable JSON.
          APIError (native):  API Response included a status > 400 or an
            `error` field in its JSON.
        """
        method: Callable[..., Response] = getattr(self.session, methodname)
        # Can raise requests.HTTPError
        response: Response = method(API_URL, **kwargs)
        if not response:  # status code > 400
            raise APIError(f"{response.status_code=}", response.content)
        try:
            data: dict[str, Any] = response.json()
        except JSONDecodeError as e:
            raise APIError("No JSON found.", response.content) from e
        if 'error' in data:
            raise APIError("'error' field in response.", response.content)
        return data

    def get(self, params: dict[str, Any]) -> Any:
        """Send GET request within the OAuth-signed session.

        Automatically specifies output in JSON (overridable).

        Arg:
          params:  Params to supplement/override the default ones.

        Returns / Raises:
          See ._api() documentation.
        """
        return self._api('get',
                         params={'format': 'json', **params})

    def post(self, params: dict[str, Any],
             tokentype: TokenType = 'csrf') -> Any:
        """Send POST request within the OAuth-signed session.

        Automatically specifies output in JSON (overridable), and sets
        the request's body (a CSRF token) through a get_token() call
        defaulting to CSRF.

        Since Response error handling is internal (through api()), in
        most cases it will not be necessary to access the returned dict.

        Args:
          params:  Params to supplement/override the default ones.
          tokentype:  A TokenType to pass to get_token().  Defaults to
            'csrf' like get_token() and the MW API.

        Returns / Raises:
          See ._api() documentation.
        """
        return self._api('post',
                         params={'format': 'json', **params},
                         data={'token': self.get_token(tokentype)})

    def get_token(self, tokentype: TokenType = 'csrf') -> str:
        R"""Request a token (CSRF by default) from the MediaWiki API.

        Args:
        tokentype:  A type of token, among the literals defined by
            TokenType.  Defaults to 'csrf' like the MW API.

        Returns:
        A string matching a validly-formatted token of the specified type.

        Raises:
        APIError from KeyError:  If the query response has no token field.
        ZBError:  If the token field is "empty" (just "+\\")
        """
        query: dict[str, Any] = self.get({'action': 'query',
                                          'meta': 'tokens',
                                          'type': tokentype})
        try:
            # How MW names all tokens.
            token: str = query['query']['tokens'][tokentype + 'token']
        except KeyError as e:
            raise APIError("No token obtained.", query) from e
        if token == R"+\\":
            raise ZBError("Empty token.")
        return token

    def getpage(self, title: str, ns: int = 0,
                must_exist: bool = False) -> Page:
        """Wrapper for pwb.title(), with optional existence check.

        Does not guarantee that page actually exists; check with
        .exists().

        title:  A str matching a valid wikipage title.
        ns:  The MW-defined number for the page's namespace.

        Returns:
          A Page with title `title` in namespace with number `ns`,
          possibly nonexistent (if not `must_exist`).

        Raises:
          ZBError:  If `must_exist` but the page does not exist.
        """
        page = Page(self.site, title=title, ns=ns)
        if must_exist and not page.exists():
            raise ZBError("Page does not exist")
        return page


# All non-PWB API calls through this!  Also PWB Site() requests.
zb = Bot(bot_auth)


def log_onwiki(event: str, logpage: str, prefix: str = "User:'zinbot/logs/",
               summary: str = "Updating logs") -> None:
    """Log an event to a page on-wiki.

    Defaults to a subpage of `User:'zinbot/logs/`.

    Args:
      event:  A string, to be appended to the page in question.
      logpage:  A string, which when appended to `prefix` forms a page's
        full title on-wiki.
      prefix:  A string to go before `title`.  To be specified if the
        log will not be in the normal place.
      summary:  A custom edit summary to use.
    """
    zb.post({'action': 'edit',
             'title': prefix + logpage,
             'appendtext': event,
             'summary': summary})


def log_local(page: Page, logfile: str) -> None:
    """Append a page's title and URL to a document in folder `logs/`.

    Args:
      page:  A Page whose title should be logged.
      logfile:  A file (extant or not) in folder `logs/`.
    """
    # NOTE: For now takes full Page as object because there's no reason
    # to duplicate calling .title() within each call.  If used in cases
    # where no Page is involved, `page` could be expanded to take
    # Page|str, only calling .title() in the former case.
    with open(f"logs/{logfile}", 'a') as f:
        f.write(
            page.title()
            + f" <{WIKI_URL}{page.title(as_url=True)}>\n"
        )

"""Functions for interacting with the MW API, including through PWB."""
# NOTE: If the bot's framework winds up taking up more files than the
# current 3 (this, `auth`, and `config`), it should probably be moved to
# a `framework` subpackage.
import json
from json.decoder import JSONDecodeError
from typing import Any, Callable, Literal

import pywikibot as pwb
from pywikibot import Page, Timestamp
from requests import Response

import config
from utils import ZBError

_API_URL = "https://test.wikipedia.org/w/api.php?"
_session = config.zb.session()
# To avoid calling anew each time `getpage` is called.  Cached
# regardless but still better to avoid repeat calls.
_site = pwb.Site()
RequestParams = dict[str, object]
# Awaiting resolution of <https://github.com/python/mypy/issues/731>.
# Till then, best for base JSON functions to return Any while calling
# functions annotate specific return types.
# ResponseJSON = dict[str, 'ResponseJSON'] | list['ResponseJSON']
TokenType = Literal['createaccount', 'csrf', 'deleteglobalaccount',
                    'login', 'patrol', 'rollback',
                    'setglobalaccountstatus', 'userrights', 'watch']


class APIError(Exception):
    """Exception raised by issues in dealing with the MediaWiki API."""

    def __init__(self, msg: str, event: object = None) -> None:
        """Saves MW API error content, if any is passed.

        Saves to logs/APIError.json if JSON-serializable,
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


def _request(methodname: Literal['get', 'post'],
             **kwargs: RequestParams) -> Any:
    """Error handling and JSON conversion for API functions.

    Routes requests through _session, which is defined privately in
    config using an auth.Authorization, and _API_URL, defined as a
    constant in this module.

    Args:
      method:  A str matching the name of a method that an OAuth1Session
        can have.
      **kwargs:  Keyword arguments to pass to `method`.

    Returns:
      An object matching the JSON structure of the relevant API
      Response.

    Raises:
      requests.HTTPError:  Issue connecting with API.
      APIError from JSONDecodeError:  API Response output was not
        decodable JSON.
      APIError (native):  API Response included a status > 400 or an
        'error' field in its JSON.
    """
    method: Callable[..., Response] = getattr(_session, methodname)
    # Can raise requests.HTTPError
    response = method(_API_URL, **kwargs)
    if not response:  # status code > 400
        raise APIError(f"{response.status_code=}", response.content)
    try:
        data = response.json()
    except JSONDecodeError as e:
        raise APIError("No JSON found.", response.content) from e
    if 'error' in data:
        raise APIError("'error' field in response.", data)
    return data


def get(params: RequestParams) -> Any:
    """Send GET request within the OAuth-signed session.

    Automatically specifies output in JSON (overridable).

    Arg:
      params:  Params to supplement/override the default ones.

    Returns / Raises:
      See ._api() documentation.
    """
    return _request('get',
                    params={'format': 'json', **params})


def post(params: RequestParams, tokentype: TokenType = 'csrf') -> Any:
    """Send POST request within the OAuth-signed session.

    Automatically specifies output in JSON (overridable), and sets the
    request's body (a CSRF token) through a get_token() call defaulting
    to CSRF.

    Since Response error handling is internal (through api()), in most
    cases it will not be necessary to access the returned dict.

    Args:
      params:  Params to supplement/override the default ones.
      tokentype:  A TokenType to pass to get_token().  Defaults to
        'csrf' like get_token() and the MW API.

    Returns / Raises:
      See ._api() documentation.
    """
    return _request('post',
                    params={'format': 'json', **params},
                    data={'token': get_token(tokentype)})


def get_token(tokentype: TokenType = 'csrf') -> str:
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
    query = get({'action': 'query',
                 'meta': 'tokens',
                 'type': tokentype})
    try:
        # How MW names all tokens:
        token: str = query['query']['tokens'][tokentype + 'token']
    except KeyError as e:
        raise APIError("No token obtained.", query) from e
    if token == R"+\\":
        raise ZBError("Empty token.")
    return token


def get_page(title: str, ns: int = 0, must_exist: bool = False) -> Page:
    """Wrapper for Page(), with optional existence check.

    Does not guarantee that page actually exists; check with .exists().

    Args:
      title:  A str matching a valid wikipage title.
      ns:  An int of the MW-defined number for the page's namespace.

    Returns:
      A Page with title `title` in namespace with number `ns`, possibly
      nonexistent (if not `must_exist`).

    Raises:
      ZBError:  If `must_exist` but the page does not exist.
    """
    page = Page(_site, title=title, ns=ns)
    if must_exist and not page.exists():
        raise ZBError("Page does not exist")
    return page


def site_time() -> Timestamp:
    """Wrapper for site server time."""
    return _site.server_time()

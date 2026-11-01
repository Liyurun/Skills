#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gsheet_ops.py - full Google Sheets operations through the Sheets API v4.

Standard library only. Five credential methods, first available wins (force one with
--auth or GSHEET_OPS_AUTH): token (GSHEET_OPS_TOKEN / _FILE), refresh (GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN), service-account (GOOGLE_APPLICATION_CREDENTIALS),
skynet (the stored Skynet login, default when nothing else is set) and gcloud. The credential
stays in memory, goes only to Google, and is never printed. `auth-check` shows the method in
use, the available methods and the granted scopes.

Usage:  python3 -B -I gsheet_ops.py <command> <spreadsheet-url-or-id> [options]
        python3 -B -I gsheet_ops.py --help
        python3 -B -I gsheet_ops.py <command> --help

Conventions
  * Rows and columns on the command line are 1-based and inclusive
    (--start 3 --end 5 means rows 3..5; columns may be letters, e.g. --start B --end D).
  * Ranges use A1 notation and may carry a sheet name: 'My Tab'!A1:C10, A:A, 2:5, A2:V.
  * Without a sheet in the range, --sheet (title or gid:<id>) is used, then the gid in the
    spreadsheet URL, then the first tab.
  * Every mutating command accepts --dry-run (prints the request, sends nothing).
  * Destructive commands need --yes.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'
DRIVE = 'https://www.googleapis.com/drive/v3/files'
TOKENINFO = 'https://oauth2.googleapis.com/tokeninfo'
TOKEN_KEY = 'GOOGLE_TOKEN'


class Fail(Exception):
    pass


# --------------------------------------------------------------------------- http / auth
OAUTH_URL = os.environ.get('GSHEET_OPS_TOKEN_URL', 'https://oauth2.googleapis.com/token')
SCOPES = 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive'
AUTH_METHODS = ('token', 'refresh', 'service-account', 'skynet', 'gcloud')
NO_CREDENTIAL = ('no Google credential found. Provide one of: GSHEET_OPS_TOKEN / GSHEET_OPS_TOKEN_FILE (access token); '
                 'GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN (OAuth refresh); '
                 'GOOGLE_APPLICATION_CREDENTIALS (service-account key file); the Skynet login (skynet-base); '
                 'or gcloud. See references/auth.md.')


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def rs256_sign(pem, data):
    """Sign with the system openssl; the private key travels over stdin, only the public JWT body touches disk."""
    try:
        d = tempfile.mkdtemp(prefix='.gsheet_', dir=os.getcwd())
    except OSError:
        d = tempfile.mkdtemp(prefix='gsheet_')
    try:
        dp = os.path.join(d, 'data')
        with open(dp, 'wb') as f:
            f.write(data)
        p = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', '/dev/stdin', dp], input=pem.encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise Fail('openssl could not sign the service-account assertion (is openssl installed and the key valid?)')
        return p.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Api:
    def __init__(self, dry=False, forced=None):
        self.dry = dry
        self.forced = forced
        self.ref = None
        self.method = None
        self._tok = None
        self._exp = 0
        self._renewed = False
        self._secrets = []

    # ---- which credential sources exist (names only, never values)
    @staticmethod
    def available():
        e = os.environ
        return {
            'token': bool(e.get('GSHEET_OPS_TOKEN') or (e.get('GSHEET_OPS_TOKEN_FILE') and os.path.isfile(e['GSHEET_OPS_TOKEN_FILE']))),
            'refresh': all(e.get(k) for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN')),
            'service-account': bool(e.get('GOOGLE_APPLICATION_CREDENTIALS') and os.path.isfile(e['GOOGLE_APPLICATION_CREDENTIALS'])),
            'skynet': shutil.which('skynet-base') is not None,
            'gcloud': shutil.which('gcloud') is not None,
        }

    def pick(self):
        forced = self.forced or os.environ.get('GSHEET_OPS_AUTH') or 'auto'
        if forced != 'auto':
            if forced not in AUTH_METHODS:
                raise Fail('unknown auth method %r; use one of: auto, %s' % (forced, ', '.join(AUTH_METHODS)))
            return forced
        av = self.available()
        for m in AUTH_METHODS:
            if av[m]:
                return m
        raise Fail(NO_CREDENTIAL)

    def token(self, fresh=False):
        if self._tok and not fresh and time.time() < self._exp:
            return self._tok
        if self.method is None:
            self.method = self.pick()
        {'token': self._from_token, 'refresh': self._from_refresh, 'service-account': self._from_sa,
         'skynet': self._from_skynet, 'gcloud': self._from_gcloud}[self.method]()
        return self._tok

    def _set(self, tok, ttl):
        self._tok = tok
        self._exp = time.time() + ttl
        self._secrets.append(tok)

    def _from_token(self):
        e = os.environ
        t = e.get('GSHEET_OPS_TOKEN', '').strip()
        if not t and e.get('GSHEET_OPS_TOKEN_FILE'):
            with open(e['GSHEET_OPS_TOKEN_FILE'], encoding='utf-8') as f:
                t = f.read().strip()
        if not t:
            raise Fail('auth method "token" selected but GSHEET_OPS_TOKEN / GSHEET_OPS_TOKEN_FILE is empty')
        self._set(t, 10 ** 9)

    def _oauth(self, fields, url=None):
        req = urllib.request.Request(url or OAUTH_URL, data=urllib.parse.urlencode(fields).encode(), method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read().decode('utf-8', 'replace'))
                msg = '%s %s' % (j.get('error', ''), j.get('error_description', ''))
            except ValueError:
                msg = 'HTTP %d' % e.code
            raise Fail('OAuth token request failed: %s' % self.scrub(msg.strip()))
        except urllib.error.URLError as e:
            raise Fail('OAuth token request failed: %s' % e.reason)

    def _from_refresh(self):
        e = os.environ
        for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN'):
            if not e.get(k):
                raise Fail('auth method "refresh" needs %s' % k)
        self._secrets += [e['GOOGLE_CLIENT_SECRET'], e['GOOGLE_REFRESH_TOKEN']]
        r = self._oauth({'grant_type': 'refresh_token', 'client_id': e['GOOGLE_CLIENT_ID'],
                         'client_secret': e['GOOGLE_CLIENT_SECRET'], 'refresh_token': e['GOOGLE_REFRESH_TOKEN']})
        if 'access_token' not in r:
            raise Fail('OAuth response had no access_token')
        self._set(r['access_token'], max(60, int(r.get('expires_in', 3600)) - 60))

    def _from_sa(self):
        path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if not path or not os.path.isfile(path):
            raise Fail('auth method "service-account" needs GOOGLE_APPLICATION_CREDENTIALS pointing to a key file')
        with open(path, encoding='utf-8') as f:
            key = json.load(f)
        if key.get('type') != 'service_account' or not key.get('private_key') or not key.get('client_email'):
            raise Fail('GOOGLE_APPLICATION_CREDENTIALS is not a service-account key file')
        self._secrets.append(key['private_key'])
        aud = os.environ.get('GSHEET_OPS_TOKEN_URL') or key.get('token_uri') or OAUTH_URL
        now = int(time.time())
        claims = {'iss': key['client_email'], 'scope': SCOPES, 'aud': aud, 'iat': now, 'exp': now + 3600}
        if os.environ.get('GSHEET_OPS_IMPERSONATE'):
            claims['sub'] = os.environ['GSHEET_OPS_IMPERSONATE']
        head = b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
        body = b64url(json.dumps(claims).encode())
        sig = b64url(rs256_sign(key['private_key'], (head + '.' + body).encode()))
        r = self._oauth({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                         'assertion': head + '.' + body + '.' + sig}, url=aud)
        if 'access_token' not in r:
            raise Fail('OAuth response had no access_token')
        self._set(r['access_token'], max(60, int(r.get('expires_in', 3600)) - 60))

    def _from_skynet(self):
        p = subprocess.run(['skynet-base', 'key', 'get', 'GOOGLE_TOKEN', '--raw'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        t = p.stdout.strip()
        if p.returncode != 0 or not t:
            raise Fail('no Skynet Google credential found. Run any `skynet-base gsheet list-sheets <url>` '
                       'once to authorize, then retry.')
        self._set(t, 300)

    def _from_gcloud(self):
        try:
            p = subprocess.run(['gcloud', 'auth', 'print-access-token'], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True)
        except OSError:
            raise Fail('auth method "gcloud" selected but gcloud is not installed')
        t = p.stdout.strip()
        if p.returncode != 0 or not t:
            raise Fail('`gcloud auth print-access-token` failed; run `gcloud auth login` first')
        self._set(t, 300)

    def scrub(self, s):
        for secret in self._secrets:
            if secret and s:
                s = s.replace(secret, '***')
        return s

    def _renew(self):
        if self.method == 'token':
            raise Fail('the access token you supplied was rejected (expired, revoked or lacking scope); supply a fresh one')
        if self.method == 'skynet':
            # A harmless read through the official CLI lets it renew or re-authorize the credential.
            if self.ref:
                subprocess.run(['skynet-base', 'gsheet', 'list-sheets', self.ref],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(['skynet-base', 'key', 'sync'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._tok = None  # refresh / service-account / gcloud mint a new token on the next token() call

    def request(self, method, url, body=None, mutating=False, raw=False):
        if self.dry and mutating:
            print(json.dumps({'dry_run': True, 'method': method, 'url': url, 'body': body},
                             ensure_ascii=False, indent=2))
            return {}
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header('Authorization', 'Bearer ' + self.token())
            if data is not None:
                req.add_header('Content-Type', 'application/json; charset=utf-8')
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    payload = r.read()
                if raw:
                    return payload
                return json.loads(payload.decode('utf-8')) if payload else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode('utf-8', 'replace')
                code = e.code
                if code == 401 and not self._renewed:
                    self._renewed = True
                    self._renew()
                    self.token(fresh=True)
                    continue
                if code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(min(60, 2 ** attempt * 2))
                    continue
                raise Fail('HTTP %d: %s' % (code, self.scrub(text)[:1800]))
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise Fail('network error: %s' % e.reason)
        raise Fail('request failed after retries')


# --------------------------------------------------------------------------- A1 / colors
def col_to_n(s):
    n = 0
    for ch in s.upper():
        n = n * 26 + ord(ch) - 64
    return n


def n_to_col(n):
    s = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def split_sheet(rng):
    if rng.startswith("'"):
        i = 1
        while i < len(rng):
            if rng[i] == "'":
                if i + 1 < len(rng) and rng[i + 1] == "'":
                    i += 2
                    continue
                break
            i += 1
        name = rng[1:i].replace("''", "'")
        rest = rng[i + 1:]
        if rest.startswith('!'):
            rest = rest[1:]
        return name, rest
    if '!' in rng:
        n, r = rng.rsplit('!', 1)
        return n, r
    return None, rng


CELL = re.compile(r'^\$?([A-Za-z]*)\$?(\d*)$')


def parse_cell(s):
    m = CELL.match(s.strip())
    if not m:
        raise Fail('bad A1 reference %r' % s)
    c, r = m.groups()
    return (col_to_n(c) if c else None, int(r) if r else None)


NAMED = {'white': 'ffffff', 'black': '000000', 'red': 'ff0000', 'green': '00ff00', 'blue': '0000ff',
         'yellow': 'ffff00', 'orange': 'ff9900', 'gray': '999999', 'grey': '999999', 'purple': '9900ff',
         'pink': 'ff00ff', 'cyan': '00ffff', 'light-gray': 'd9d9d9', 'light-yellow': 'fff2cc',
         'light-orange': 'f8cbad', 'light-red': 'f4cccc', 'light-green': 'd9ead3', 'light-blue': 'cfe2f3'}


def rgb(c):
    if isinstance(c, dict):
        return c
    s = str(c).strip().lower()
    s = NAMED.get(s, s).lstrip('#')
    if len(s) == 3:
        s = ''.join(ch * 2 for ch in s)
    if not re.fullmatch(r'[0-9a-f]{6}', s):
        raise Fail('bad color %r (use #RRGGBB, #RGB or a name such as light-yellow)' % c)
    return {'red': int(s[0:2], 16) / 255, 'green': int(s[2:4], 16) / 255, 'blue': int(s[4:6], 16) / 255}


def hexof(c):
    c = c or {}
    return '#%02x%02x%02x' % tuple(int(round(c.get(k, 0) * 255)) for k in ('red', 'green', 'blue'))


def parse_ss(ref):
    m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', ref)
    sid = m.group(1) if m else ref.strip()
    g = re.search(r'[#?&]gid=(\d+)', ref)
    return sid, (int(g.group(1)) if g else None)


# --------------------------------------------------------------------------- context
class Ctx:
    def __init__(self, a):
        self.a = a
        self.api = Api(dry=getattr(a, 'dry_run', False), forced=getattr(a, 'auth', None))
        self.id = None
        self.gid = None
        ss = getattr(a, 'ss', None)
        if ss:
            self.id, self.gid = parse_ss(ss)
            self.api.ref = ss
        self._meta = None

    def meta(self):
        if self._meta is None:
            fields = 'properties(title,locale,timeZone),sheets.properties,namedRanges'
            self._meta = self.api.request('GET', '%s/%s?fields=%s' % (SHEETS, self.id, urllib.parse.quote(fields)))
        return self._meta

    def sheets(self):
        return [s['properties'] for s in self.meta().get('sheets', [])]

    def sheet(self, ref=None):
        if ref is None:
            ref = getattr(self.a, 'sheet', None)
        ps = self.sheets()
        if ref in (None, ''):
            if self.gid is not None:
                for p in ps:
                    if p['sheetId'] == self.gid:
                        return p
            return ps[0]
        ref = str(ref)
        for p in ps:
            if p['title'] == ref:
                return p
        if ref.startswith('gid:') or ref.isdigit():
            gid = int(ref.replace('gid:', ''))
            for p in ps:
                if p['sheetId'] == gid:
                    return p
        raise Fail('sheet not found: %r; available: %s' % (ref, [p['title'] for p in ps]))

    def batch(self, requests, chunk=200):
        out = []
        for i in range(0, len(requests), chunk):
            r = self.api.request('POST', '%s/%s:batchUpdate' % (SHEETS, self.id),
                                 {'requests': requests[i:i + chunk]}, mutating=True)
            out.extend(r.get('replies', []))
        self._meta = None
        return out

    def grid(self, rng):
        name, rest = split_sheet(rng)
        sh = self.sheet(name) if name else self.sheet()
        parts = rest.split(':')
        a = parse_cell(parts[0])
        b = parse_cell(parts[1]) if len(parts) > 1 else a
        g = {'sheetId': sh['sheetId']}
        (c1, r1), (c2, r2) = a, b
        if r1:
            g['startRowIndex'] = r1 - 1
        if r2:
            g['endRowIndex'] = r2
        if c1:
            g['startColumnIndex'] = c1 - 1
        if c2:
            g['endColumnIndex'] = c2
        return g

    def qualify(self, rng):
        name, rest = split_sheet(rng)
        if name is None:
            name = self.sheet()['title']
        q = "'%s'" % name.replace("'", "''")
        return q + ('!' + rest if rest else '')

    def drange(self, start, end):
        dim = 'ROWS' if str(self.a.dim).lower().startswith('row') else 'COLUMNS'

        def ix(v):
            v = str(v)
            if dim == 'COLUMNS' and re.fullmatch(r'[A-Za-z]+', v):
                return col_to_n(v)
            return int(v)
        s = ix(start)
        e = ix(end) if end is not None else s
        return {'sheetId': self.sheet()['sheetId'], 'dimension': dim, 'startIndex': s - 1, 'endIndex': e}

    def need_yes(self, what):
        if not getattr(self.a, 'yes', False) and not getattr(self.a, 'dry_run', False):
            raise Fail('%s is destructive: re-run with --yes after the user has confirmed (or use --dry-run).' % what)

    def values_url(self, rng, suffix='', params=None):
        u = '%s/%s/values/%s%s' % (SHEETS, self.id, urllib.parse.quote(rng, safe=''), suffix)
        return u + ('?' + urllib.parse.urlencode(params) if params else '')


def emit(a, obj):
    if getattr(a, 'dry_run', False):
        return
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    out = getattr(a, 'output', None)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(json.dumps({'written': out, 'bytes': len(text.encode('utf-8'))}, ensure_ascii=False))
    else:
        print(text)


def load_json_arg(a, inline='values', file_attr='file', what='JSON'):
    s = getattr(a, inline, None)
    f = getattr(a, file_attr, None)
    if f:
        with open(f, encoding='utf-8') as fh:
            return json.load(fh)
    if s is not None:
        return json.loads(s)
    raise Fail('provide --%s or --%s' % (inline, file_attr))


# --------------------------------------------------------------------------- formats
def build_format(d):
    fmt = {}
    f = []
    if d.get('bg') is not None:
        fmt['backgroundColor'] = rgb(d['bg'])
        f.append('userEnteredFormat.backgroundColor')
    tf = {}
    for key, name in (('fg', 'foregroundColor'), ('bold', 'bold'), ('italic', 'italic'), ('underline', 'underline'),
                      ('strike', 'strikethrough'), ('font', 'fontFamily'), ('size', 'fontSize')):
        if d.get(key) is not None:
            tf[name] = rgb(d[key]) if key == 'fg' else d[key]
            f.append('userEnteredFormat.textFormat.' + name)
    if tf:
        fmt['textFormat'] = tf
    for key, name in (('halign', 'horizontalAlignment'), ('valign', 'verticalAlignment'), ('wrap', 'wrapStrategy')):
        if d.get(key) is not None:
            fmt[name] = str(d[key]).upper()
            f.append('userEnteredFormat.' + name)
    if d.get('numfmt') is not None:
        nf = {'type': str(d['numfmt']).upper()}
        if d.get('pattern'):
            nf['pattern'] = d['pattern']
        fmt['numberFormat'] = nf
        f.append('userEnteredFormat.numberFormat')
    if d.get('rotation') is not None:
        fmt['textRotation'] = {'angle': int(d['rotation'])}
        f.append('userEnteredFormat.textRotation')
    if not f:
        raise Fail('no format option given (e.g. --bg, --bold, --halign, --numfmt)')
    return fmt, f


def repeat_cell(grid, d):
    fmt, f = build_format(d)
    return {'repeatCell': {'range': grid, 'cell': {'userEnteredFormat': fmt}, 'fields': ','.join(f)}}


FORMAT_ARGS = [
    (['--bg'], {'help': 'background color: #RRGGBB, #RGB or name (light-yellow...)'}),
    (['--fg'], {'help': 'font color'}),
    (['--bold'], {'dest': 'bold', 'action': 'store_const', 'const': True, 'default': None}),
    (['--no-bold'], {'dest': 'bold', 'action': 'store_const', 'const': False}),
    (['--italic'], {'dest': 'italic', 'action': 'store_const', 'const': True, 'default': None}),
    (['--no-italic'], {'dest': 'italic', 'action': 'store_const', 'const': False}),
    (['--underline'], {'dest': 'underline', 'action': 'store_const', 'const': True, 'default': None}),
    (['--no-underline'], {'dest': 'underline', 'action': 'store_const', 'const': False}),
    (['--strike'], {'dest': 'strike', 'action': 'store_const', 'const': True, 'default': None}),
    (['--no-strike'], {'dest': 'strike', 'action': 'store_const', 'const': False}),
    (['--font'], {'help': 'font family, e.g. Arial'}),
    (['--size'], {'type': int, 'help': 'font size in pt'}),
    (['--halign'], {'choices': ['LEFT', 'CENTER', 'RIGHT', 'left', 'center', 'right']}),
    (['--valign'], {'choices': ['TOP', 'MIDDLE', 'BOTTOM', 'top', 'middle', 'bottom']}),
    (['--wrap'], {'choices': ['OVERFLOW_CELL', 'CLIP', 'WRAP', 'overflow_cell', 'clip', 'wrap']}),
    (['--numfmt'], {'help': 'NUMBER|PERCENT|CURRENCY|DATE|TIME|DATE_TIME|SCIENTIFIC|TEXT'}),
    (['--pattern'], {'help': 'number format pattern, e.g. 0.00%% or yyyy-mm-dd'}),
    (['--rotation'], {'type': int, 'help': 'text rotation angle -90..90'}),
]


# --------------------------------------------------------------------------- commands
COMMANDS = {}


def cmd(name, help_, args=(), needs_ss=True):
    def deco(fn):
        COMMANDS[name] = (fn, help_, list(args), needs_ss)
        return fn
    return deco


# ---- auth / read -----------------------------------------------------------------
@cmd('auth-check', 'show which credential method is used, which methods are available, granted scopes and remaining lifetime (never prints a credential)', needs_ss=False)
def c_auth(ctx):
    api = ctx.api
    avail = Api.available()
    tok = api.token()
    body = urllib.parse.urlencode({'access_token': tok}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKENINFO, data=body, method='POST'), timeout=30) as r:
            info = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise Fail('credential rejected by Google (HTTP %d) using method %r' % (e.code, api.method))
    scopes = info.get('scope', '').split()
    emit(ctx.a, {'method_in_use': api.method, 'available_methods': avail, 'scopes': scopes,
                 'expires_in_seconds': info.get('expires_in'),
                 'can_edit_sheets': 'https://www.googleapis.com/auth/spreadsheets' in scopes,
                 'can_use_drive': 'https://www.googleapis.com/auth/drive' in scopes})


@cmd('info', 'spreadsheet title/locale/time zone, every tab with size, freeze, hidden, tab color, and named ranges')
def c_info(ctx):
    m = ctx.meta()
    tabs = []
    for p in ctx.sheets():
        gp = p.get('gridProperties', {})
        tabs.append({'sheetId': p['sheetId'], 'title': p['title'], 'index': p.get('index'), 'rows': gp.get('rowCount'),
                     'cols': gp.get('columnCount'), 'frozenRows': gp.get('frozenRowCount', 0),
                     'frozenCols': gp.get('frozenColumnCount', 0), 'hidden': p.get('hidden', False),
                     'tabColor': hexof(p['tabColorStyle']['rgbColor']) if p.get('tabColorStyle', {}).get('rgbColor') else None})
    emit(ctx.a, {'title': m['properties'].get('title'), 'locale': m['properties'].get('locale'),
                 'timeZone': m['properties'].get('timeZone'), 'tabs': tabs, 'namedRanges': m.get('namedRanges', [])})


@cmd('inspect', 'merges, conditional formats, filters, banding, protected ranges, charts, groups of a tab (or all tabs)')
def c_inspect(ctx):
    fields = ('sheets(properties(sheetId,title),merges,conditionalFormats,protectedRanges,basicFilter,bandedRanges,'
              'filterViews,charts(chartId,spec(title)),rowGroups,columnGroups)')
    r = ctx.api.request('GET', '%s/%s?fields=%s' % (SHEETS, ctx.id, urllib.parse.quote(fields)))
    sheets = r.get('sheets', [])
    if getattr(ctx.a, 'sheet', None):
        gid = ctx.sheet()['sheetId']
        sheets = [s for s in sheets if s['properties']['sheetId'] == gid]
    emit(ctx.a, {'sheets': sheets})


@cmd('read', 'read values of a range', [
    (['--range'], {}), (['--render'], {'default': 'FORMATTED_VALUE', 'help': 'FORMATTED_VALUE|UNFORMATTED_VALUE|FORMULA'}),
    (['--dim'], {'default': 'ROWS'}), (['--datetime'], {'help': 'SERIAL_NUMBER|FORMATTED_STRING'})])
def c_read(ctx):
    a = ctx.a
    rng = ctx.qualify(a.range or '')
    p = {'valueRenderOption': a.render, 'majorDimension': a.dim.upper()}
    if a.datetime:
        p['dateTimeRenderOption'] = a.datetime
    emit(a, ctx.api.request('GET', ctx.values_url(rng, params=p)))


@cmd('batch-read', 'read several ranges at once', [(['--ranges'], {'required': True, 'help': 'comma separated A1 ranges'}),
                                                    (['--render'], {'default': 'FORMATTED_VALUE'})])
def c_batch_read(ctx):
    a = ctx.a
    qs = [('ranges', ctx.qualify(r.strip())) for r in a.ranges.split(',')] + [('valueRenderOption', a.render)]
    emit(a, ctx.api.request('GET', '%s/%s/values:batchGet?%s' % (SHEETS, ctx.id, urllib.parse.urlencode(qs))))


@cmd('get-format', 'read cell formatting of a range (summary by background color, or --full)', [
    (['--range'], {'required': True}), (['--full'], {'action': 'store_true'})])
def c_get_format(ctx):
    a = ctx.a
    rng = ctx.qualify(a.range)
    fields = ('sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values(userEnteredFormat('
              'backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy,numberFormat)))))')
    r = ctx.api.request('GET', '%s/%s?ranges=%s&fields=%s' % (SHEETS, ctx.id, urllib.parse.quote(rng, safe=''),
                                                               urllib.parse.quote(fields)))
    if a.full:
        emit(a, r)
        return
    groups = {}
    for sh in r.get('sheets', []):
        for d in sh.get('data', []):
            r0, c0 = d.get('startRow', 0), d.get('startColumn', 0)
            for i, row in enumerate(d.get('rowData', [])):
                for j, cell in enumerate(row.get('values', [])):
                    bg = cell.get('userEnteredFormat', {}).get('backgroundColor')
                    key = hexof(bg) if bg else 'none'
                    g = groups.setdefault(key, {'count': 0, 'sample': []})
                    g['count'] += 1
                    if len(g['sample']) < 8:
                        g['sample'].append('%s%d' % (n_to_col(c0 + j + 1), r0 + i + 1))
    emit(a, {'background_colors': groups})


# ---- values ---------------------------------------------------------------------
def _grow(ctx, sh, need_rows, need_cols):
    gp = sh.get('gridProperties', {})
    rows, cols = gp.get('rowCount', 0), gp.get('columnCount', 0)
    if need_rows > rows or need_cols > cols:
        ctx.batch([{'updateSheetProperties': {
            'properties': {'sheetId': sh['sheetId'], 'gridProperties': {
                'rowCount': max(rows, need_rows), 'columnCount': max(cols, need_cols)}},
            'fields': 'gridProperties.rowCount,gridProperties.columnCount'}}])


@cmd('write', 'overwrite values from --values/--file (JSON 2D array); grows the grid and chunks rows automatically', [
    (['--range'], {'default': 'A1'}), (['--values'], {}), (['--file'], {}),
    (['--raw'], {'action': 'store_true', 'help': 'RAW input (no formula/date parsing); default USER_ENTERED'}),
    (['--no-grow'], {'action': 'store_true'}), (['--chunk-rows'], {'type': int, 'default': 500}),
    (['--dim'], {'default': 'ROWS'})])
def c_write(ctx):
    a = ctx.a
    values = load_json_arg(a)
    name, rest = split_sheet(a.range)
    sh = ctx.sheet(name) if name else ctx.sheet()
    c0, r0 = parse_cell(rest.split(':')[0])
    c0, r0 = c0 or 1, r0 or 1
    ncols = max((len(r) for r in values), default=0)
    if not a.no_grow and not a.dry_run:
        _grow(ctx, sh, r0 + len(values) - 1, c0 + ncols - 1)
    mode = 'RAW' if a.raw else 'USER_ENTERED'
    done = 0
    for i in range(0, max(len(values), 1), a.chunk_rows):
        part = values[i:i + a.chunk_rows]
        rng = "'%s'!%s%d" % (sh['title'].replace("'", "''"), n_to_col(c0), r0 + i)
        r = ctx.api.request('PUT', ctx.values_url(rng, params={'valueInputOption': mode}),
                            {'majorDimension': a.dim.upper(), 'values': part}, mutating=True)
        done += r.get('updatedCells', 0)
    emit(a, {'updatedCells': done, 'sheet': sh['title']})


@cmd('append', 'append rows after the last row of the table (rows are inserted)', [
    (['--range'], {'default': ''}), (['--values'], {}), (['--file'], {}), (['--raw'], {'action': 'store_true'})])
def c_append(ctx):
    a = ctx.a
    values = load_json_arg(a)
    rng = ctx.qualify(a.range or '')
    r = ctx.api.request('POST', ctx.values_url(rng, ':append', {
        'valueInputOption': 'RAW' if a.raw else 'USER_ENTERED', 'insertDataOption': 'INSERT_ROWS'}),
        {'majorDimension': 'ROWS', 'values': values}, mutating=True)
    emit(a, r.get('updates', r))


@cmd('clear', 'clear values of a range (formats stay)', [(['--range'], {'required': True}), (['--yes'], {'action': 'store_true'})])
def c_clear(ctx):
    ctx.need_yes('clear')
    r = ctx.api.request('POST', ctx.values_url(ctx.qualify(ctx.a.range), ':clear'), {}, mutating=True)
    emit(ctx.a, r)


@cmd('batch-write', 'write many ranges: --file JSON [{"range":"A1","values":[[...]]},...]', [
    (['--file'], {'required': True}), (['--raw'], {'action': 'store_true'})])
def c_batch_write(ctx):
    a = ctx.a
    items = load_json_arg(a, inline='nothing')
    data = [{'range': ctx.qualify(i['range']), 'majorDimension': i.get('majorDimension', 'ROWS'), 'values': i['values']} for i in items]
    r = ctx.api.request('POST', '%s/%s/values:batchUpdate' % (SHEETS, ctx.id),
                        {'valueInputOption': 'RAW' if a.raw else 'USER_ENTERED', 'data': data}, mutating=True)
    emit(a, {'totalUpdatedCells': r.get('totalUpdatedCells')})


@cmd('batch-clear', 'clear several ranges', [(['--ranges'], {'required': True}), (['--yes'], {'action': 'store_true'})])
def c_batch_clear(ctx):
    ctx.need_yes('batch-clear')
    rs = [ctx.qualify(x.strip()) for x in ctx.a.ranges.split(',')]
    emit(ctx.a, ctx.api.request('POST', '%s/%s/values:batchClear' % (SHEETS, ctx.id), {'ranges': rs}, mutating=True))


@cmd('find-replace', 'find and replace text', [
    (['--find'], {'required': True}), (['--replace'], {'required': True}), (['--range'], {}),
    (['--all-sheets'], {'action': 'store_true'}), (['--regex'], {'action': 'store_true'}),
    (['--match-case'], {'action': 'store_true'}), (['--entire-cell'], {'action': 'store_true'}),
    (['--formulas'], {'action': 'store_true', 'help': 'also search formulas'})])
def c_find_replace(ctx):
    a = ctx.a
    fr = {'find': a.find, 'replacement': a.replace, 'matchCase': a.match_case, 'matchEntireCell': a.entire_cell,
          'searchByRegex': a.regex, 'includeFormulas': a.formulas}
    if a.range:
        fr['range'] = ctx.grid(a.range)
    elif a.all_sheets:
        fr['allSheets'] = True
    else:
        fr['sheetId'] = ctx.sheet()['sheetId']
    emit(a, ctx.batch([{'findReplace': fr}]))


# ---- spreadsheet level ----------------------------------------------------------
@cmd('create', 'create a spreadsheet', [(['--title'], {'required': True}), (['--sheets'], {'help': 'comma separated tab names'}),
                                        (['--locale'], {}), (['--timezone'], {})], needs_ss=False)
def c_create(ctx):
    a = ctx.a
    props = {'title': a.title}
    if a.locale:
        props['locale'] = a.locale
    if a.timezone:
        props['timeZone'] = a.timezone
    body = {'properties': props}
    if a.sheets:
        body['sheets'] = [{'properties': {'title': t.strip()}} for t in a.sheets.split(',')]
    r = ctx.api.request('POST', SHEETS, body, mutating=True)
    emit(a, {'spreadsheetId': r.get('spreadsheetId'), 'url': r.get('spreadsheetUrl'),
             'tabs': [s['properties'] for s in r.get('sheets', [])]})


@cmd('set-properties', 'rename the spreadsheet / change locale or time zone', [
    (['--title'], {}), (['--locale'], {}), (['--timezone'], {})])
def c_set_props(ctx):
    a = ctx.a
    p, f = {}, []
    for k, n in (('title', 'title'), ('locale', 'locale'), ('timezone', 'timeZone')):
        if getattr(a, k):
            p[n] = getattr(a, k)
            f.append(n)
    if not f:
        raise Fail('give --title, --locale or --timezone')
    emit(a, ctx.batch([{'updateSpreadsheetProperties': {'properties': p, 'fields': ','.join(f)}}]))


@cmd('export', 'export the whole spreadsheet through Drive (xlsx, ods, pdf, csv=first tab, tsv, html=zip)', [
    (['--format'], {'default': 'xlsx', 'choices': ['xlsx', 'ods', 'pdf', 'csv', 'tsv', 'html']}),
    (['--output'], {'required': True, 'help': 'local file path (keep it inside the working directory)'})])
def c_export(ctx):
    mime = {'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'ods': 'application/x-vnd.oasis.opendocument.spreadsheet', 'pdf': 'application/pdf',
            'csv': 'text/csv', 'tsv': 'text/tab-separated-values', 'html': 'application/zip'}[ctx.a.format]
    data = ctx.api.request('GET', '%s/%s/export?mimeType=%s' % (DRIVE, ctx.id, urllib.parse.quote(mime, safe='')), raw=True)
    os.makedirs(os.path.dirname(os.path.abspath(ctx.a.output)), exist_ok=True)
    with open(ctx.a.output, 'wb') as f:
        f.write(data)
    print(json.dumps({'written': ctx.a.output, 'bytes': len(data)}))


@cmd('copy-spreadsheet', 'make a copy of the whole spreadsheet in Drive', [(['--title'], {'required': True}), (['--folder'], {})])
def c_copy_ss(ctx):
    body = {'name': ctx.a.title}
    if ctx.a.folder:
        body['parents'] = [ctx.a.folder]
    r = ctx.api.request('POST', '%s/%s/copy' % (DRIVE, ctx.id), body, mutating=True)
    emit(ctx.a, r)


# ---- tabs -----------------------------------------------------------------------
@cmd('add-sheet', 'add a tab', [
    (['--title'], {'required': True}), (['--index'], {'type': int}), (['--rows'], {'type': int}), (['--cols'], {'type': int}),
    (['--tab-color'], {}), (['--hidden'], {'action': 'store_true'}),
    (['--freeze-rows'], {'type': int}), (['--freeze-cols'], {'type': int})])
def c_add_sheet(ctx):
    a = ctx.a
    p = {'title': a.title}
    if a.index is not None:
        p['index'] = a.index
    gp = {}
    for k, n in (('rows', 'rowCount'), ('cols', 'columnCount'), ('freeze_rows', 'frozenRowCount'), ('freeze_cols', 'frozenColumnCount')):
        if getattr(a, k) is not None:
            gp[n] = getattr(a, k)
    if gp:
        p['gridProperties'] = gp
    if a.tab_color:
        p['tabColorStyle'] = {'rgbColor': rgb(a.tab_color)}
    if a.hidden:
        p['hidden'] = True
    emit(a, ctx.batch([{'addSheet': {'properties': p}}]))


def _props(ctx, props, fields):
    sh = ctx.sheet()
    props = dict(props, sheetId=sh['sheetId'])
    return ctx.batch([{'updateSheetProperties': {'properties': props, 'fields': fields}}])


@cmd('rename-sheet', 'rename a tab', [(['--new-title'], {'required': True})])
def c_rename_sheet(ctx):
    emit(ctx.a, _props(ctx, {'title': ctx.a.new_title}, 'title'))


@cmd('duplicate-sheet', 'duplicate a tab inside the spreadsheet', [(['--new-title'], {}), (['--index'], {'type': int})])
def c_dup_sheet(ctx):
    d = {'sourceSheetId': ctx.sheet()['sheetId']}
    if ctx.a.new_title:
        d['newSheetName'] = ctx.a.new_title
    if ctx.a.index is not None:
        d['insertSheetIndex'] = ctx.a.index
    emit(ctx.a, ctx.batch([{'duplicateSheet': d}]))


@cmd('delete-sheet', 'delete a tab', [(['--yes'], {'action': 'store_true'})])
def c_del_sheet(ctx):
    ctx.need_yes('delete-sheet')
    emit(ctx.a, ctx.batch([{'deleteSheet': {'sheetId': ctx.sheet()['sheetId']}}]))


@cmd('copy-sheet-to', 'copy a tab into another spreadsheet', [(['--dest'], {'required': True, 'help': 'destination spreadsheet url/id'})])
def c_copy_to(ctx):
    dest, _ = parse_ss(ctx.a.dest)
    r = ctx.api.request('POST', '%s/%s/sheets/%d:copyTo' % (SHEETS, ctx.id, ctx.sheet()['sheetId']),
                        {'destinationSpreadsheetId': dest}, mutating=True)
    emit(ctx.a, r)


@cmd('move-sheet', 'move a tab to a position (0-based index)', [(['--index'], {'type': int, 'required': True})])
def c_move_sheet(ctx):
    emit(ctx.a, _props(ctx, {'index': ctx.a.index}, 'index'))


@cmd('hide-sheet', 'hide a tab')
def c_hide_sheet(ctx):
    emit(ctx.a, _props(ctx, {'hidden': True}, 'hidden'))


@cmd('show-sheet', 'unhide a tab')
def c_show_sheet(ctx):
    emit(ctx.a, _props(ctx, {'hidden': False}, 'hidden'))


@cmd('tab-color', 'set the tab color', [(['--color'], {'required': True})])
def c_tab_color(ctx):
    emit(ctx.a, _props(ctx, {'tabColorStyle': {'rgbColor': rgb(ctx.a.color)}}, 'tabColorStyle'))


@cmd('resize-grid', 'set the number of rows/columns of a tab', [(['--rows'], {'type': int}), (['--cols'], {'type': int})])
def c_resize(ctx):
    gp, f = {}, []
    if ctx.a.rows:
        gp['rowCount'] = ctx.a.rows
        f.append('gridProperties.rowCount')
    if ctx.a.cols:
        gp['columnCount'] = ctx.a.cols
        f.append('gridProperties.columnCount')
    if not f:
        raise Fail('give --rows and/or --cols')
    emit(ctx.a, _props(ctx, {'gridProperties': gp}, ','.join(f)))


@cmd('freeze', 'freeze rows/columns (0 unfreezes)', [(['--rows'], {'type': int}), (['--cols'], {'type': int})])
def c_freeze(ctx):
    gp, f = {}, []
    if ctx.a.rows is not None:
        gp['frozenRowCount'] = ctx.a.rows
        f.append('gridProperties.frozenRowCount')
    if ctx.a.cols is not None:
        gp['frozenColumnCount'] = ctx.a.cols
        f.append('gridProperties.frozenColumnCount')
    if not f:
        raise Fail('give --rows and/or --cols')
    emit(ctx.a, _props(ctx, {'gridProperties': gp}, ','.join(f)))


# ---- rows / columns ---------------------------------------------------------------
DIM = (['--dim'], {'required': True, 'choices': ['rows', 'cols', 'row', 'col', 'columns']})
START = (['--start'], {'required': True, 'help': '1-based first row number or column letter'})
END = (['--end'], {'help': 'last row/column inclusive (default: same as --start)'})


@cmd('insert-dim', 'insert rows/columns before --start', [DIM, START, (['--count'], {'type': int, 'default': 1}),
                                                          (['--inherit-before'], {'action': 'store_true'})])
def c_insert_dim(ctx):
    a = ctx.a
    s = ctx.drange(a.start, a.start)
    s['endIndex'] = s['startIndex'] + a.count
    emit(a, ctx.batch([{'insertDimension': {'range': s, 'inheritFromBefore': a.inherit_before}}]))


@cmd('delete-dim', 'delete rows/columns', [DIM, START, END, (['--yes'], {'action': 'store_true'})])
def c_delete_dim(ctx):
    ctx.need_yes('delete-dim')
    emit(ctx.a, ctx.batch([{'deleteDimension': {'range': ctx.drange(ctx.a.start, ctx.a.end)}}]))


@cmd('move-dim', 'move rows/columns so they start before --to', [DIM, START, END, (['--to'], {'required': True, 'help': 'destination position (1-based, before this row/col)'})])
def c_move_dim(ctx):
    dest = ctx.drange(ctx.a.to, ctx.a.to)['startIndex']
    emit(ctx.a, ctx.batch([{'moveDimension': {'source': ctx.drange(ctx.a.start, ctx.a.end), 'destinationIndex': dest}}]))


def _hide(ctx, hidden):
    emit(ctx.a, ctx.batch([{'updateDimensionProperties': {
        'range': ctx.drange(ctx.a.start, ctx.a.end), 'properties': {'hiddenByUser': hidden}, 'fields': 'hiddenByUser'}}]))


@cmd('hide-dim', 'hide rows/columns', [DIM, START, END])
def c_hide_dim(ctx):
    _hide(ctx, True)


@cmd('show-dim', 'unhide rows/columns', [DIM, START, END])
def c_show_dim(ctx):
    _hide(ctx, False)


@cmd('size', 'set row height / column width in pixels', [DIM, START, END, (['--pixels'], {'type': int, 'required': True})])
def c_size(ctx):
    emit(ctx.a, ctx.batch([{'updateDimensionProperties': {
        'range': ctx.drange(ctx.a.start, ctx.a.end), 'properties': {'pixelSize': ctx.a.pixels}, 'fields': 'pixelSize'}}]))


@cmd('autosize', 'auto-fit rows/columns to content', [DIM, START, END])
def c_autosize(ctx):
    emit(ctx.a, ctx.batch([{'autoResizeDimensions': {'dimensions': ctx.drange(ctx.a.start, ctx.a.end)}}]))


@cmd('group-dim', 'group rows/columns (collapsible outline)', [DIM, START, END])
def c_group(ctx):
    emit(ctx.a, ctx.batch([{'addDimensionGroup': {'range': ctx.drange(ctx.a.start, ctx.a.end)}}]))


@cmd('ungroup-dim', 'remove a row/column group', [DIM, START, END])
def c_ungroup(ctx):
    emit(ctx.a, ctx.batch([{'deleteDimensionGroup': {'range': ctx.drange(ctx.a.start, ctx.a.end)}}]))


# ---- formatting -------------------------------------------------------------------
@cmd('format', 'apply cell formatting to a range', [(['--range'], {'required': True})] + FORMAT_ARGS)
def c_format(ctx):
    a = ctx.a
    d = {k: getattr(a, k) for k in ('bg', 'fg', 'bold', 'italic', 'underline', 'strike', 'font', 'size', 'halign',
                                    'valign', 'wrap', 'numfmt', 'pattern', 'rotation')}
    emit(a, ctx.batch([repeat_cell(ctx.grid(a.range), d)]))


@cmd('format-batch', 'apply many formats in few API calls: --spec JSON [{"range":"A2:A9","format":{"bg":"#FFF2CC","bold":true}},...]', [
    (['--spec'], {'required': True}), (['--chunk'], {'type': int, 'default': 200})])
def c_format_batch(ctx):
    with open(ctx.a.spec, encoding='utf-8') as f:
        items = json.load(f)
    reqs = [repeat_cell(ctx.grid(i['range']), i['format']) for i in items]
    ctx.batch(reqs, chunk=ctx.a.chunk)
    emit(ctx.a, {'formatted_ranges': len(reqs), 'api_calls': (len(reqs) + ctx.a.chunk - 1) // ctx.a.chunk})


@cmd('clear-format', 'reset all formatting of a range', [(['--range'], {'required': True})])
def c_clear_format(ctx):
    emit(ctx.a, ctx.batch([{'repeatCell': {'range': ctx.grid(ctx.a.range), 'cell': {}, 'fields': 'userEnteredFormat'}}]))


@cmd('borders', 'set borders', [
    (['--range'], {'required': True}),
    (['--edges'], {'default': 'all', 'help': 'comma list: all,outer,top,bottom,left,right,inner-h,inner-v'}),
    (['--style'], {'default': 'SOLID', 'help': 'SOLID|SOLID_MEDIUM|SOLID_THICK|DASHED|DOTTED|DOUBLE|NONE'}),
    (['--color'], {'default': 'black'})])
def c_borders(ctx):
    a = ctx.a
    b = {'style': a.style.upper(), 'color': rgb(a.color)}
    names = {'top': 'top', 'bottom': 'bottom', 'left': 'left', 'right': 'right',
             'inner-h': 'innerHorizontal', 'inner-v': 'innerVertical'}
    edges = [e.strip() for e in a.edges.split(',')]
    keys = set()
    for e in edges:
        if e == 'all':
            keys |= set(names)
        elif e == 'outer':
            keys |= {'top', 'bottom', 'left', 'right'}
        elif e in names:
            keys.add(e)
        else:
            raise Fail('unknown edge %r' % e)
    req = {'range': ctx.grid(a.range)}
    for k in keys:
        req[names[k]] = b
    emit(a, ctx.batch([{'updateBorders': req}]))


@cmd('merge', 'merge cells', [(['--range'], {'required': True}), (['--type'], {'default': 'all', 'choices': ['all', 'rows', 'columns']})])
def c_merge(ctx):
    t = {'all': 'MERGE_ALL', 'rows': 'MERGE_ROWS', 'columns': 'MERGE_COLUMNS'}[ctx.a.type]
    emit(ctx.a, ctx.batch([{'mergeCells': {'range': ctx.grid(ctx.a.range), 'mergeType': t}}]))


@cmd('unmerge', 'unmerge cells', [(['--range'], {'required': True})])
def c_unmerge(ctx):
    emit(ctx.a, ctx.batch([{'unmergeCells': {'range': ctx.grid(ctx.a.range)}}]))


@cmd('banding-add', 'alternating row colors', [(['--range'], {'required': True}), (['--header'], {'default': '#4285f4'}),
                                               (['--first'], {'default': '#ffffff'}), (['--second'], {'default': '#f3f3f3'})])
def c_banding_add(ctx):
    a = ctx.a
    rp = {'headerColor': rgb(a.header), 'firstBandColor': rgb(a.first), 'secondBandColor': rgb(a.second)}
    emit(a, ctx.batch([{'addBanding': {'bandedRange': {'range': ctx.grid(a.range), 'rowProperties': rp}}}]))


@cmd('banding-delete', 'remove alternating colors by id (see `inspect`)', [(['--id'], {'type': int, 'required': True}), (['--yes'], {'action': 'store_true'})])
def c_banding_del(ctx):
    ctx.need_yes('banding-delete')
    emit(ctx.a, ctx.batch([{'deleteBanding': {'bandedRangeId': ctx.a.id}}]))


CF_FORMAT_ARGS = [x for x in FORMAT_ARGS if x[0][0] in ('--bg', '--fg', '--bold', '--no-bold', '--italic', '--no-italic', '--strike', '--no-strike')]
CF_ARGS = [
    (['--range'], {'required': True}),
    (['--type'], {'required': True, 'help': 'e.g. NUMBER_GREATER, NUMBER_BETWEEN, TEXT_CONTAINS, TEXT_EQ, BLANK, NOT_BLANK, CUSTOM_FORMULA, DATE_BEFORE'}),
    (['--values'], {'help': 'comma separated condition values (formula for CUSTOM_FORMULA, e.g. =$B2>100)'})] + CF_FORMAT_ARGS


def _cf_rule(ctx):
    a = ctx.a
    fmt = {}
    if a.bg:
        fmt['backgroundColor'] = rgb(a.bg)
    tf = {}
    if a.fg:
        tf['foregroundColor'] = rgb(a.fg)
    for k, n in (('bold', 'bold'), ('italic', 'italic'), ('strike', 'strikethrough')):
        if getattr(a, k) is not None:
            tf[n] = getattr(a, k)
    if tf:
        fmt['textFormat'] = tf
    if not fmt:
        raise Fail('give --bg and/or --fg/--bold ...')
    cond = {'type': a.type.upper()}
    if a.values:
        vals = [a.values] if a.type.upper() == 'CUSTOM_FORMULA' else [v.strip() for v in a.values.split(',')]
        cond['values'] = [{'userEnteredValue': v} for v in vals]
    return {'ranges': [ctx.grid(a.range)], 'booleanRule': {'condition': cond, 'format': fmt}}


@cmd('cf-add', 'add a conditional-format rule (boolean condition)', CF_ARGS + [(['--index'], {'type': int, 'default': 0})])
def c_cf_add(ctx):
    emit(ctx.a, ctx.batch([{'addConditionalFormatRule': {'rule': _cf_rule(ctx), 'index': ctx.a.index}}]))


@cmd('cf-update', 'replace the conditional-format rule at --index (see cf-list) with a new boolean rule', CF_ARGS + [(['--index'], {'type': int, 'required': True})])
def c_cf_update(ctx):
    emit(ctx.a, ctx.batch([{'updateConditionalFormatRule': {'sheetId': ctx.sheet()['sheetId'], 'index': ctx.a.index, 'rule': _cf_rule(ctx)}}]))


@cmd('cf-gradient', 'add a color-scale conditional format', [
    (['--range'], {'required': True}), (['--min-color'], {'default': '#ffffff'}), (['--max-color'], {'default': '#57bb8a'}),
    (['--mid-color'], {}), (['--index'], {'type': int, 'default': 0})])
def c_cf_gradient(ctx):
    a = ctx.a
    g = {'minpoint': {'color': rgb(a.min_color), 'type': 'MIN'}, 'maxpoint': {'color': rgb(a.max_color), 'type': 'MAX'}}
    if a.mid_color:
        g['midpoint'] = {'color': rgb(a.mid_color), 'type': 'PERCENTILE', 'value': '50'}
    emit(a, ctx.batch([{'addConditionalFormatRule': {'rule': {'ranges': [ctx.grid(a.range)], 'gradientRule': g}, 'index': a.index}}]))


@cmd('cf-list', 'list conditional-format rules of a tab with their index')
def c_cf_list(ctx):
    sid = ctx.sheet()['sheetId']
    fields = 'sheets(properties(sheetId,title),conditionalFormats)'
    r = ctx.api.request('GET', '%s/%s?fields=%s' % (SHEETS, ctx.id, urllib.parse.quote(fields)))
    for s in r.get('sheets', []):
        if s['properties']['sheetId'] == sid:
            emit(ctx.a, [{'index': i, 'rule': x} for i, x in enumerate(s.get('conditionalFormats', []))])
            return
    emit(ctx.a, [])


@cmd('cf-delete', 'delete a conditional-format rule by index (see cf-list)', [(['--index'], {'type': int, 'required': True}), (['--yes'], {'action': 'store_true'})])
def c_cf_del(ctx):
    ctx.need_yes('cf-delete')
    emit(ctx.a, ctx.batch([{'deleteConditionalFormatRule': {'sheetId': ctx.sheet()['sheetId'], 'index': ctx.a.index}}]))


# ---- filters / validation -----------------------------------------------------------
@cmd('filter-set', 'set the basic filter on a range (header row included)', [(['--range'], {'required': True}), (['--criteria'], {'help': 'JSON file: {"<colIndex>": FilterCriteria}'})])
def c_filter_set(ctx):
    f = {'range': ctx.grid(ctx.a.range)}
    if ctx.a.criteria:
        with open(ctx.a.criteria, encoding='utf-8') as fh:
            f['criteria'] = json.load(fh)
    emit(ctx.a, ctx.batch([{'setBasicFilter': {'filter': f}}]))


@cmd('filter-clear', 'clear the basic filter of a tab')
def c_filter_clear(ctx):
    emit(ctx.a, ctx.batch([{'clearBasicFilter': {'sheetId': ctx.sheet()['sheetId']}}]))


@cmd('filter-view-add', 'add a named filter view', [(['--range'], {'required': True}), (['--title'], {'required': True})])
def c_fv_add(ctx):
    emit(ctx.a, ctx.batch([{'addFilterView': {'filter': {'title': ctx.a.title, 'range': ctx.grid(ctx.a.range)}}}]))


@cmd('filter-view-delete', 'delete a filter view by id (see inspect)', [(['--id'], {'type': int, 'required': True}), (['--yes'], {'action': 'store_true'})])
def c_fv_del(ctx):
    ctx.need_yes('filter-view-delete')
    emit(ctx.a, ctx.batch([{'deleteFilterView': {'filterId': ctx.a.id}}]))


@cmd('validate-set', 'set data validation (dropdown, checkbox, number/date/text rules, custom formula)', [
    (['--range'], {'required': True}), (['--list'], {'help': 'dropdown items, comma separated'}),
    (['--list-range'], {'help': 'dropdown from a range, e.g. Config!A2:A20'}), (['--checkbox'], {'action': 'store_true'}),
    (['--type'], {'help': 'raw condition type, e.g. NUMBER_BETWEEN, DATE_AFTER, TEXT_CONTAINS, CUSTOM_FORMULA'}),
    (['--values'], {'help': 'comma separated values for --type'}), (['--message'], {'help': 'input hint text'}),
    (['--strict'], {'action': 'store_true', 'help': 'reject invalid input (default: warning only)'}),
    (['--no-dropdown'], {'action': 'store_true'})])
def c_validate_set(ctx):
    a = ctx.a
    if a.list:
        cond = {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': v.strip()} for v in a.list.split(',')]}
    elif a.list_range:
        cond = {'type': 'ONE_OF_RANGE', 'values': [{'userEnteredValue': '=' + ctx.qualify(a.list_range)}]}
    elif a.checkbox:
        cond = {'type': 'BOOLEAN'}
    elif a.type:
        cond = {'type': a.type.upper()}
        if a.values:
            vals = [a.values] if a.type.upper() == 'CUSTOM_FORMULA' else [v.strip() for v in a.values.split(',')]
            cond['values'] = [{'userEnteredValue': v} for v in vals]
    else:
        raise Fail('give one of --list, --list-range, --checkbox or --type')
    rule = {'condition': cond, 'strict': a.strict, 'showCustomUi': not a.no_dropdown}
    if a.message:
        rule['inputMessage'] = a.message
    emit(a, ctx.batch([{'setDataValidation': {'range': ctx.grid(a.range), 'rule': rule}}]))


@cmd('validate-clear', 'remove data validation', [(['--range'], {'required': True})])
def c_validate_clear(ctx):
    emit(ctx.a, ctx.batch([{'setDataValidation': {'range': ctx.grid(ctx.a.range)}}]))


# ---- data operations ------------------------------------------------------------------
@cmd('sort', 'sort a range (exclude the header row from --range)', [
    (['--range'], {'required': True}), (['--by'], {'required': True, 'help': 'e.g. "B:desc,C:asc" (column letters)'})])
def c_sort(ctx):
    specs = []
    for part in ctx.a.by.split(','):
        col, _, order = part.partition(':')
        specs.append({'dimensionIndex': col_to_n(col.strip()) - 1,
                      'sortOrder': 'DESCENDING' if order.strip().lower().startswith('d') else 'ASCENDING'})
    emit(ctx.a, ctx.batch([{'sortRange': {'range': ctx.grid(ctx.a.range), 'sortSpecs': specs}}]))


@cmd('dedupe', 'delete duplicate rows by comparing columns', [
    (['--range'], {'required': True}), (['--columns'], {'required': True, 'help': 'column letters to compare, e.g. A,C'}),
    (['--yes'], {'action': 'store_true'})])
def c_dedupe(ctx):
    ctx.need_yes('dedupe')
    g = ctx.grid(ctx.a.range)
    cols = [{'sheetId': g['sheetId'], 'dimension': 'COLUMNS', 'startIndex': col_to_n(c) - 1, 'endIndex': col_to_n(c)}
            for c in ctx.a.columns.replace(' ', '').split(',')]
    emit(ctx.a, ctx.batch([{'deleteDuplicates': {'range': g, 'comparisonColumns': cols}}]))


@cmd('trim', 'trim leading/trailing whitespace', [(['--range'], {'required': True})])
def c_trim(ctx):
    emit(ctx.a, ctx.batch([{'trimWhitespace': {'range': ctx.grid(ctx.a.range)}}]))


@cmd('text-to-columns', 'split text of a column into columns', [
    (['--range'], {'required': True}), (['--delimiter'], {'default': 'COMMA', 'help': 'COMMA|SEMICOLON|PERIOD|SPACE|AUTODETECT or a custom character'})])
def c_t2c(ctx):
    d = ctx.a.delimiter
    t = {'delimiterType': d.upper()} if d.upper() in ('COMMA', 'SEMICOLON', 'PERIOD', 'SPACE', 'AUTODETECT') else {'delimiterType': 'CUSTOM', 'delimiter': d}
    emit(ctx.a, ctx.batch([{'textToColumns': dict({'source': ctx.grid(ctx.a.range)}, **t)}]))


@cmd('copy-paste', 'copy a range to another place', [
    (['--src'], {'required': True}), (['--dest'], {'required': True}),
    (['--paste'], {'default': 'PASTE_NORMAL', 'help': 'PASTE_NORMAL|PASTE_VALUES|PASTE_FORMAT|PASTE_NO_BORDERS|PASTE_FORMULA|PASTE_DATA_VALIDATION|PASTE_CONDITIONAL_FORMATTING'})])
def c_copy_paste(ctx):
    emit(ctx.a, ctx.batch([{'copyPaste': {'source': ctx.grid(ctx.a.src), 'destination': ctx.grid(ctx.a.dest), 'pasteType': ctx.a.paste.upper()}}]))


@cmd('cut-paste', 'move a range to another place (destination = top-left cell)', [
    (['--src'], {'required': True}), (['--dest'], {'required': True}), (['--paste'], {'default': 'PASTE_NORMAL'})])
def c_cut_paste(ctx):
    d = ctx.grid(ctx.a.dest)
    dest = {'sheetId': d['sheetId'], 'rowIndex': d.get('startRowIndex', 0), 'columnIndex': d.get('startColumnIndex', 0)}
    emit(ctx.a, ctx.batch([{'cutPaste': {'source': ctx.grid(ctx.a.src), 'destination': dest, 'pasteType': ctx.a.paste.upper()}}]))


# ---- protection / names / charts / raw --------------------------------------------------
@cmd('protect', 'protect a range or a whole tab', [
    (['--range'], {}), (['--description'], {}), (['--warning-only'], {'action': 'store_true'}),
    (['--editors'], {'help': 'comma separated emails allowed to edit'})])
def c_protect(ctx):
    a = ctx.a
    pr = {'range': ctx.grid(a.range) if a.range else {'sheetId': ctx.sheet()['sheetId']}, 'warningOnly': a.warning_only}
    if a.description:
        pr['description'] = a.description
    if a.editors:
        pr['editors'] = {'users': [e.strip() for e in a.editors.split(',')]}
    emit(a, ctx.batch([{'addProtectedRange': {'protectedRange': pr}}]))


@cmd('unprotect', 'remove protection by id (see inspect)', [(['--id'], {'type': int, 'required': True}), (['--yes'], {'action': 'store_true'})])
def c_unprotect(ctx):
    ctx.need_yes('unprotect')
    emit(ctx.a, ctx.batch([{'deleteProtectedRange': {'protectedRangeId': ctx.a.id}}]))


@cmd('named-range-add', 'create a named range', [(['--name'], {'required': True}), (['--range'], {'required': True})])
def c_nr_add(ctx):
    emit(ctx.a, ctx.batch([{'addNamedRange': {'namedRange': {'name': ctx.a.name, 'range': ctx.grid(ctx.a.range)}}}]))


@cmd('named-range-delete', 'delete a named range by id (see info)', [(['--id'], {'required': True}), (['--yes'], {'action': 'store_true'})])
def c_nr_del(ctx):
    ctx.need_yes('named-range-delete')
    emit(ctx.a, ctx.batch([{'deleteNamedRange': {'namedRangeId': ctx.a.id}}]))


@cmd('chart-add', 'add a chart from a ChartSpec JSON file (see references/recipes.md)', [
    (['--spec'], {'required': True}), (['--anchor'], {'default': 'A1', 'help': 'top-left cell of the chart'}),
    (['--width'], {'type': int, 'default': 600}), (['--height'], {'type': int, 'default': 371})])
def c_chart_add(ctx):
    a = ctx.a
    with open(a.spec, encoding='utf-8') as f:
        spec = json.load(f)
    g = ctx.grid(a.anchor)
    pos = {'overlayPosition': {'anchorCell': {'sheetId': g['sheetId'], 'rowIndex': g.get('startRowIndex', 0),
                                              'columnIndex': g.get('startColumnIndex', 0)},
                               'widthPixels': a.width, 'heightPixels': a.height}}
    emit(a, ctx.batch([{'addChart': {'chart': {'spec': spec, 'position': pos}}}]))


@cmd('chart-delete', 'delete a chart by id (see inspect)', [(['--id'], {'type': int, 'required': True}), (['--yes'], {'action': 'store_true'})])
def c_chart_del(ctx):
    ctx.need_yes('chart-delete')
    emit(ctx.a, ctx.batch([{'deleteEmbeddedObject': {'objectId': ctx.a.id}}]))


@cmd('batch-update', 'send raw batchUpdate requests (any Sheets API request: pivots, slicers, tables, ...)', [
    (['--requests'], {'required': True, 'help': 'JSON file containing a list of request objects'}), (['--yes'], {'action': 'store_true'})])
def c_raw(ctx):
    ctx.need_yes('batch-update (raw)')
    with open(ctx.a.requests, encoding='utf-8') as f:
        reqs = json.load(f)
    if not isinstance(reqs, list) or not all(isinstance(r, dict) and len(r) == 1 for r in reqs):
        raise Fail('--requests must be a list of single-key request objects')
    emit(ctx.a, ctx.batch(reqs))


@cmd('insert-range', 'insert blank cells and shift the existing cells down or right', [
    (['--range'], {'required': True}), (['--shift'], {'default': 'down', 'choices': ['down', 'right']})])
def c_insert_range(ctx):
    emit(ctx.a, ctx.batch([{'insertRange': {'range': ctx.grid(ctx.a.range),
                                            'shiftDimension': 'ROWS' if ctx.a.shift == 'down' else 'COLUMNS'}}]))


@cmd('delete-range', 'delete cells and shift the rest up or left', [
    (['--range'], {'required': True}), (['--shift'], {'default': 'up', 'choices': ['up', 'left']}), (['--yes'], {'action': 'store_true'})])
def c_delete_range(ctx):
    ctx.need_yes('delete-range')
    emit(ctx.a, ctx.batch([{'deleteRange': {'range': ctx.grid(ctx.a.range),
                                            'shiftDimension': 'ROWS' if ctx.a.shift == 'up' else 'COLUMNS'}}]))


@cmd('autofill', 'fill a series/pattern: --range holds the pattern plus the cells to fill; or --source with --dim and --length', [
    (['--range'], {}), (['--source'], {'help': 'pattern cells, e.g. A1:A2'}),
    (['--dim'], {'default': 'rows', 'choices': ['rows', 'cols', 'row', 'col', 'columns']}),
    (['--length'], {'type': int, 'help': 'how many rows/columns to fill beyond the source'})])
def c_autofill(ctx):
    a = ctx.a
    if a.source:
        if not a.length:
            raise Fail('--length is required with --source')
        req = {'sourceAndDestination': {'source': ctx.grid(a.source), 'fillLength': a.length,
                                        'dimension': 'ROWS' if a.dim.startswith('row') else 'COLUMNS'}}
    elif a.range:
        req = {'range': ctx.grid(a.range)}
    else:
        raise Fail('give --range, or --source with --length')
    emit(a, ctx.batch([{'autoFill': req}]))


@cmd('note-set', 'set the note (comment bubble) of every cell in a range; empty --text clears it', [
    (['--range'], {'required': True}), (['--text'], {'required': True})])
def c_note_set(ctx):
    emit(ctx.a, ctx.batch([{'repeatCell': {'range': ctx.grid(ctx.a.range), 'cell': {'note': ctx.a.text}, 'fields': 'note'}}]))


@cmd('append-dim', 'append empty rows or columns at the end of a tab', [
    (['--dim'], {'required': True, 'choices': ['rows', 'cols', 'row', 'col', 'columns']}),
    (['--count'], {'type': int, 'required': True})])
def c_append_dim(ctx):
    dim = 'ROWS' if ctx.a.dim.startswith('row') else 'COLUMNS'
    emit(ctx.a, ctx.batch([{'appendDimension': {'sheetId': ctx.sheet()['sheetId'], 'dimension': dim, 'length': ctx.a.count}}]))


@cmd('gridlines', 'show or hide the gridlines of a tab', [(['--mode'], {'required': True, 'choices': ['show', 'hide']})])
def c_gridlines(ctx):
    emit(ctx.a, _props(ctx, {'gridProperties': {'hideGridlines': ctx.a.mode == 'hide'}}, 'gridProperties.hideGridlines'))


@cmd('group-toggle', 'collapse or expand a row/column group', [
    DIM, START, END, (['--collapsed'], {'required': True, 'choices': ['true', 'false']}), (['--depth'], {'type': int, 'default': 1})])
def c_group_toggle(ctx):
    emit(ctx.a, ctx.batch([{'updateDimensionGroup': {
        'dimensionGroup': {'range': ctx.drange(ctx.a.start, ctx.a.end), 'depth': ctx.a.depth,
                           'collapsed': ctx.a.collapsed == 'true'}, 'fields': 'collapsed'}}]))


@cmd('named-range-update', 'rename a named range or change its range', [
    (['--id'], {'required': True}), (['--name'], {}), (['--range'], {})])
def c_nr_update(ctx):
    a = ctx.a
    nr, f = {'namedRangeId': a.id}, []
    if a.name:
        nr['name'] = a.name
        f.append('name')
    if a.range:
        nr['range'] = ctx.grid(a.range)
        f.append('range')
    if not f:
        raise Fail('give --name and/or --range')
    emit(a, ctx.batch([{'updateNamedRange': {'namedRange': nr, 'fields': ','.join(f)}}]))


@cmd('protect-update', 'change the description, warning-only mode or editors of a protected range', [
    (['--id'], {'type': int, 'required': True}), (['--description'], {}),
    (['--warning-only'], {'dest': 'warning_only', 'action': 'store_const', 'const': True, 'default': None}),
    (['--no-warning-only'], {'dest': 'warning_only', 'action': 'store_const', 'const': False}),
    (['--editors'], {'help': 'comma separated emails (replaces the editor list)'})])
def c_protect_update(ctx):
    a = ctx.a
    pr, f = {'protectedRangeId': a.id}, []
    if a.description is not None:
        pr['description'] = a.description
        f.append('description')
    if a.warning_only is not None:
        pr['warningOnly'] = a.warning_only
        f.append('warningOnly')
    if a.editors:
        pr['editors'] = {'users': [e.strip() for e in a.editors.split(',')]}
        f.append('editors')
    if not f:
        raise Fail('give --description, --warning-only/--no-warning-only or --editors')
    emit(a, ctx.batch([{'updateProtectedRange': {'protectedRange': pr, 'fields': ','.join(f)}}]))


@cmd('chart-update', 'replace the spec of an existing chart (by id, see inspect)', [
    (['--id'], {'type': int, 'required': True}), (['--spec'], {'required': True})])
def c_chart_update(ctx):
    with open(ctx.a.spec, encoding='utf-8') as f:
        spec = json.load(f)
    emit(ctx.a, ctx.batch([{'updateChartSpec': {'chartId': ctx.a.id, 'spec': spec}}]))


@cmd('export-tab', 'export ONE tab as csv/tsv/pdf/xlsx (by --sheet or the gid of the URL)', [
    (['--format'], {'default': 'csv', 'choices': ['csv', 'tsv', 'pdf', 'xlsx']}),
    (['--output'], {'required': True, 'help': 'local file path (keep it inside the working directory)'})])
def c_export_tab(ctx):
    gid = ctx.sheet()['sheetId']
    url = 'https://docs.google.com/spreadsheets/d/%s/export?format=%s&gid=%d' % (ctx.id, ctx.a.format, gid)
    data = ctx.api.request('GET', url, raw=True)
    os.makedirs(os.path.dirname(os.path.abspath(ctx.a.output)), exist_ok=True)
    with open(ctx.a.output, 'wb') as f:
        f.write(data)
    print(json.dumps({'written': ctx.a.output, 'bytes': len(data), 'sheetId': gid}))


# --------------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog='gsheet_ops.py', description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', metavar='<command>')
    for name, (fn, help_, args, needs_ss) in COMMANDS.items():
        sp = sub.add_parser(name, help=help_, description=help_)
        if needs_ss:
            sp.add_argument('ss', help='spreadsheet URL or id (a gid in the URL selects the default tab)')
        if needs_ss:
            sp.add_argument('--sheet', help='tab title or gid:<id> (default: gid of the URL, else first tab)')
        sp.add_argument('--dry-run', action='store_true', help='print mutating requests instead of sending them')
        sp.add_argument('--auth', choices=['auto'] + list(AUTH_METHODS), help='credential method (default: auto = first available; env GSHEET_OPS_AUTH)')
        have = {f for flags, _ in args for f in flags}
        if '--output' not in have:
            sp.add_argument('--output', help='write the JSON result to this file')
        if '--yes' not in have and name in ('write',):
            pass
        for flags, kw in args:
            sp.add_argument(*flags, **kw)
        sp.set_defaults(func=fn)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not a.command:
        p.print_help()
        return 0
    ctx = Ctx(a)
    try:
        a.func(ctx)
    except Fail as e:
        sys.stderr.write('error: %s\n' % e)
        return 1
    except (OSError, ValueError, KeyError) as e:
        sys.stderr.write('error: %s: %s\n' % (type(e).__name__, ctx.api.scrub(str(e))))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

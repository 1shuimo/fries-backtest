#!/usr/bin/env python3
"""Send the daily monitor report to every channel that has its secret set (stdlib only).

Env vars (GitHub repo Settings -> Secrets and variables -> Actions):
  SERVERCHAN_KEY            Server酱 SendKey -> 微信 (https://sct.ftqq.com)
  PUSHPLUS_TOKEN            PushPlus token   -> 微信 (https://www.pushplus.plus)
  BARK_URL                  Bark 推送地址，如 https://api.day.app/xxxx -> iPhone
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  SMTP_HOST SMTP_USER SMTP_PASS MAIL_TO [SMTP_PORT=465]   e.g. smtp.qq.com / smtp.gmail.com (app password)
Usage: notify.py REPORT.md SUMMARY.txt [LINK] [--html EMAIL.html] [--text EMAIL.txt]
With --html the email is sent as HTML (plain text from --text, else REPORT.md, as the fallback part);
Telegram also uses the --text version, which reads better than Markdown tables on a phone."""
import json
import os
import re
import smtplib
import sys
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path


def post(url, data, form=False):
    if form:
        body = urllib.parse.urlencode(data).encode()
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    else:
        body = json.dumps(data).encode()
        headers = {'Content-Type': 'application/json; charset=utf-8'}
    req = urllib.request.Request(url, body, headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read(1024 * 1024).decode('utf-8', 'replace')


class NotificationError(Exception):
    """The provider did not confirm acceptance of the notification."""


def verify_response(channel, result):
    status, body = result
    if not 200 <= status < 300:
        raise NotificationError('HTTP failure')
    payload = json.loads(body)
    field, expected = ('ok', True) if channel == 'telegram' else (
        'code', 0 if channel == 'serverchan' else 200)
    # Strict types reject false as code=0 and 1 as ok=true.
    if (not isinstance(payload, dict)
            or type(payload.get(field)) is not type(expected)
            or payload[field] != expected):
        raise NotificationError('API failure')


def main():
    args, opts, it = [], {}, iter(sys.argv[1:])
    for x in it:
        if x in ('--html', '--text'):
            opts[x[2:]] = next(it, '')
        else:
            args.append(x)
    report = Path(args[0]).read_text(encoding='utf-8')
    title = Path(args[1]).read_text(encoding='utf-8').strip()
    link = args[2] if len(args) > 2 else ''
    read = lambda k: Path(opts[k]).read_text(encoding='utf-8') if opts.get(k) and os.path.exists(opts[k]) else ''
    html_body, text_body = read('html'), read('text') or report
    e = os.environ.get
    sent, failed = [], []

    def attempt(name, fn):
        try:
            result = fn()
            if name != 'email':
                verify_response(name, result)
            print(name, 'ok')
            sent.append(name)
        except Exception as exc:  # one broken channel must not block the others
            # Provider bodies and exception messages can contain tokens or addresses.
            print(name, 'FAILED', type(exc).__name__)
            failed.append(name)

    if e('SERVERCHAN_KEY'):
        key = e('SERVERCHAN_KEY')
        m = re.match(r'sctp(\d+)t', key)  # Server酱³ keys carry the uid
        host = f'https://{m.group(1)}.push.ft07.com/send/{key}.send' if m else f'https://sctapi.ftqq.com/{key}.send'
        attempt('serverchan', lambda: post(host, {'title': title[:100], 'desp': report}, form=True))
    if e('PUSHPLUS_TOKEN'):
        attempt('pushplus', lambda: post('https://www.pushplus.plus/send', {
            'token': e('PUSHPLUS_TOKEN'), 'title': title[:100], 'content': report, 'template': 'markdown'}))
    if e('BARK_URL'):
        attempt('bark', lambda: post(e('BARK_URL').rstrip('/'), {
            'title': '每日盯盘', 'body': title, 'group': 'fries', 'url': link}))
    if e('TELEGRAM_BOT_TOKEN') and e('TELEGRAM_CHAT_ID'):
        attempt('telegram', lambda: post(f"https://api.telegram.org/bot{e('TELEGRAM_BOT_TOKEN')}/sendMessage", {
            'chat_id': e('TELEGRAM_CHAT_ID'), 'text': (title + '\n\n' + text_body)[:4000], 'disable_web_page_preview': True}))
    if e('SMTP_HOST') and e('SMTP_USER') and e('SMTP_PASS') and e('MAIL_TO'):
        def mail():
            plain = MIMEText(text_body + (f'\n\n{link}\n' if link else ''), 'plain', 'utf-8')
            if html_body:
                msg = MIMEMultipart('alternative')
                msg.attach(plain)
                msg.attach(MIMEText(html_body, 'html', 'utf-8'))
            else:
                msg = plain
            msg['Subject'] = title
            msg['From'] = formataddr(('盯盘提醒', e('SMTP_USER')))
            msg['To'] = e('MAIL_TO')
            with smtplib.SMTP_SSL(e('SMTP_HOST'), int(e('SMTP_PORT') or 465), timeout=30) as s:
                s.login(e('SMTP_USER'), e('SMTP_PASS'))
                refused = s.sendmail(e('SMTP_USER'), [x.strip() for x in e('MAIL_TO').split(',')], msg.as_string())
                if refused:
                    raise NotificationError('SMTP recipients refused')
            return 'ok'
        attempt('email', mail)
    print('sent:', sent or 'none', 'failed:', failed or 'none')
    if not sent and not failed:
        print('::warning::没有配置任何通知渠道（SERVERCHAN_KEY / PUSHPLUS_TOKEN / BARK_URL / TELEGRAM_* / SMTP_*）')
    if failed or not sent:
        sys.exit(1)


if __name__ == '__main__':
    main()


#!/usr/bin/env python3
"""The mails of the owner automation pack, as a kernel without a host sends them (contract 0066 §4.2).

    python3 tests/automation_mail.py --out <dir> [--site demo-grosir --site demo-manpower --site demo-fnb]

For each site: a local SMTP sink (stdlib, no TLS, no AUTH) is started, the site is copied with `notify.email` switched
to `driver: smtp` on that sink, and tests/day_one.py runs the site's scenarios against it. Every mail the kernel sent
is written to <dir>/<site>/all/NNN.eml, and one mail to the owner per notice kind of the pack is copied to
<dir>/<site>/<kind>.eml and, decoded, to <dir>/<site>/<kind>.txt. Exit 1 when a kind the site should mail is missing or
a scenario failed.

Not part of CI: day_one already checks every notice in the inbox (title, text, link, recipients); this run adds the
mail itself. The older `notify_email` job of a `channel: email` step needs STARTTLS and fails against this sink, so the
sink holds only the notice mails.
"""
import argparse
import email
import email.policy
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# notice kind -> what its subject (the notice title) looks like
KINDS = {
    "demo-grosir": {
        "app.stock.stock_low": r"barang di bawah batas stok minimum$",
        "app.procurement.reorder_draft": r"^Draft pesanan pembelian untuk ",
        "app.invoicing.invoice_due": r"^Tagih hari ini: ",
        "app.invoicing.invoice_overdue": r"faktur baru lewat jatuh tempo$",
        "app.users.daily_summary": r"^Ringkasan harian ",
        "app.users.weekly_summary": r"^Ringkasan mingguan ",
        "app.users.monthly_summary": r"^Ringkasan bulanan ",
    },
    "demo-manpower": {
        "app.payroll.payroll_ready": r"^Periode gaji Alih daya uji pengingat siap diproses$",
        "app.payroll.payroll_due": r"^Gaji Alih daya uji pengingat dibayar ",
    },
    "demo-fnb": {
        "app.stock.stock_negative": r"^Stok Bahan uji stok minus minus$",
    },
}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        def w(s):
            self.wfile.write((s + "\r\n").encode())
            self.wfile.flush()
        w("220 sink ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            up = line.decode("utf-8", "replace").strip().upper()
            if up.startswith("EHLO"):
                w("250-sink")
                w("250 8BITMIME")
            elif up.startswith("DATA"):
                w("354 go")
                data = b""
                while True:
                    part = self.rfile.readline()
                    if not part or part in (b".\r\n", b".\n"):
                        break
                    data += part[1:] if part.startswith(b"..") else part
                with self.server.lock:
                    self.server.n += 1
                    with open(os.path.join(self.server.out, f"{self.server.n:03d}.eml"), "wb") as f:
                        f.write(data)
                w("250 stored")
            elif up.startswith("QUIT"):
                w("221 bye")
                return
            else:
                w("250 ok")


class Sink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_sink(out):
    os.makedirs(out, exist_ok=True)
    s = Sink(("127.0.0.1", 0), Handler)
    s.out, s.n, s.lock = out, 0, threading.Lock()
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def run_site(site, out):
    """Run day_one for `site` on a copy that mails through a sink. Returns (scenario failures, {kind: path or None})."""
    for m in [m for m in sys.modules if m.startswith("day_one")]:
        del sys.modules[m]
    sys.argv = ["day_one"]
    import day_one
    all_dir = os.path.join(out, site, "all")
    shutil.rmtree(os.path.join(out, site), ignore_errors=True)
    sink = start_sink(all_dir)
    port = sink.server_address[1]
    work = tempfile.mkdtemp(prefix="automation-mail-")
    copy = os.path.join(work, site)
    shutil.copytree(os.path.join(day_one.ROOT, "examples", "sites", site), copy)
    path = os.path.join(copy, "system.rk")
    text = open(path).read()
    text, n = re.subn(r"email: \{driver: log, from: ([^}]*)\}",
                      lambda m: "email: {driver: smtp, host: 127.0.0.1, port: %d, from: '%s'}" % (port, m.group(1).strip()), text)
    if n != 1:
        sys.exit(f"{site}: system.rk has no `notify.email: {{driver: log, from: ...}}` line to switch to smtp")
    open(path, "w").write(text)

    base = day_one.Kernel

    class MailKernel(base):
        def __init__(self, s, state_dir, site_dir=None):
            super().__init__(s, state_dir, site_dir=copy)

        def stop(self):
            if self.db:   # the queue mails after the scenarios return: wait until the sink is quiet
                last, quiet = -1, 0
                while quiet < 6:
                    quiet = quiet + 1 if sink.n == last else 0
                    last = sink.n
                    time.sleep(0.5)
            super().stop()

    day_one.Kernel = MailKernel
    sys.argv = ["day_one", "--site", site]
    code = 0
    try:
        day_one.main()
    except SystemExit as e:
        code = e.code or 0
    failed = [r for r in day_one.RESULTS if r[0] == "fail"]
    for st, name, detail in failed:
        print(f"FAIL  {site}: {name}  {detail}"[:400])
    found = {}
    for kind, pattern in KINDS.get(site, {}).items():
        found[kind] = None
        for name in sorted(os.listdir(all_dir)):
            msg = email.message_from_bytes(open(os.path.join(all_dir, name), "rb").read(), policy=email.policy.default)
            if "owner@" in str(msg["To"]) and re.search(pattern, str(msg["Subject"])):
                shutil.copy(os.path.join(all_dir, name), os.path.join(out, site, kind + ".eml"))
                with open(os.path.join(out, site, kind + ".txt"), "w") as f:
                    f.write(f"From: {msg['From']}\nTo: {msg['To']}\nSubject: {msg['Subject']}\nDate: {msg['Date']}\n\n{msg.get_content()}")
                found[kind] = name
                break
    sink.shutdown()
    shutil.rmtree(work, ignore_errors=True)
    print(f"{site}: {sink.n} mail(s) in the sink, day_one exit {code}, kinds: " + ", ".join(f"{k}={'ok' if v else 'MISSING'}" for k, v in found.items()))
    return failed, found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="directory for the mails")
    ap.add_argument("--site", action="append", choices=sorted(KINDS), help="default: every site of the pack")
    a = ap.parse_args()
    bad = False
    for site in a.site or sorted(KINDS):
        failed, found = run_site(site, os.path.abspath(a.out))
        bad = bad or bool(failed) or not all(found.values())
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

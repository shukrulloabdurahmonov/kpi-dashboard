#!/usr/bin/env python3
"""Generate the KPI_PASSWORD_HASH value for the droplet's kpi.env.

    /usr/bin/python3 deploy/make_password_hash.py
"""
import getpass

from werkzeug.security import generate_password_hash

pw = getpass.getpass("Dashboard password: ")
pw2 = getpass.getpass("Repeat: ")
if pw != pw2:
    raise SystemExit("Passwords do not match")
if len(pw) < 8:
    raise SystemExit("Use at least 8 characters")
print(generate_password_hash(pw, method="pbkdf2:sha256"))

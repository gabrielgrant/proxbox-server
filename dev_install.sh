#! /bin/sh

virtualenv --no-site-packages env
source env/bin/activate
pip install -r requirements_to_freeze.txt

#!/usr/bin/env python
# coding: utf-8
import logging
from logging.handlers import TimedRotatingFileHandler
import os, sys, commands
from flask import Flask, jsonify, abort, request

def set_log(logger):
    log_file = '/tmp/icmping_server.log'
    if log_file == None:
        log_file = '/tmp/mtr.log'
    file_dir = os.path.split(log_file)[0]
    if not os.path.isdir(file_dir):
        os.makedirs(file_dir)
    if not os.path.exists(log_file):
        os.system('touch %s ' % log_file)
    formater = '%(asctime)s %(levelname)s %(process)d %(filename)s %(funcName)s %(lineno)d %(message)s'
    logger.setLevel(logging.INFO)
    handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=0)
    handler.setFormatter(logging.Formatter(formater))
    logger.addHandler(handler)
    return logger

app = Flask(__name__, static_folder='graph')
@app.route('/api/mtr/<string:IP>', methods=['GET'])
def get_mtr(IP):
    if len(IP.split('.')) != 4:
       return "%s not *.*.*.*" % IP
    cmd = 'mtr -r -n -i 0.5 %s' % IP
    status, out = commands.getstatusoutput(cmd)
    return out.replace('\n', '<br>').replace(' ', '&nbsp;')
if __name__ == '__main__':
    logger = logging.getLogger()
    logger = set_log(logger=logger)
    host = '0.0.0.0'
    port = 8001
    app.run(host=host, port=port)

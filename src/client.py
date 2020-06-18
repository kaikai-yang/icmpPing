#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os, sys, commands, re, time, datetime, getopt
import json, socket, platform, subprocess, logging
from multiprocessing import Pool
import urllib2, requests, configparser
from decimal import Decimal
from statsd import StatsClient
from logging.handlers import TimedRotatingFileHandler
root_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "../../")
os.chdir(root_dir)

def parse_config(args):
    config_file = ''
    if len(args) == 0:
        print 'please use main.py -c <config>'
        sys.exit(2)
    try:
        opts, args = getopt.getopt(args, "h:c:", ["help", "config="])
    except getopt.GetoptError as err:
        print "please use main.py -c <config>"
        sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print 'please use main.py -c <config>'
            sys.exit(2)
        elif opt in ("-c", "--config"):
            config_file = arg
        else:
            print "please use main.py -c <config>"
            sys.exit(2)
    return config_file


def set_log(logger, log_file):
    log_file = log_file.replace('"', '').replace("'", '')
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

class Monitor():
    def __init__(self, cliParam):
        ## 用作收集网络数据的worker
        self.pool = Pool(processes=3)
        ##
        self.cliParam = cliParam
        ##init influxdb statd
        influxdb_config = cliParam["influxdb_config"]
        self.sdc = StatsClient(
            host=check_format(influxdb_config.get('host', '')),
            port=influxdb_config.getint('port', 8120),
            prefix="icmpping",
            maxudpsize=512,
            ipv6=False
        )
        self.tmpdata = {}
    def start(self):
        fping_cmd = '%s %s' % (cliParam["fping_cmd"], " ".join(cliParam["ip_list"]))
        retry = 0
        while retry < cliParam["fping_retry_max"]:
            runtime = str(datetime.datetime.now())
            status, out = commands.getstatusoutput(fping_cmd)
            if status == 0 or status == 256:
                break
            else:
                logger.error("status:%s,info:%s" %(status,out))
                logger.warn('fping retry ..')
                time.sleep(cliParam["fping_retry_interval"])
                retry += 1
        if retry > cliParam["fping_retry_max"]:
            logger.error('fping retry count > retry_max')
            sys.exit()

        list_mtr_ips = []
        for info in out.split('\n'):
            #print info
            cur_time = time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time()))
            target = info.split(':')[0].strip()
            if self.tmpdata.get(target) == None:
                self.tmpdata[target] = {}
            self.tmpdata['time'] = int(time.time() * 1000)
            self.tmpdata[target]['hostname'] = check_format(cliParam["targets_info"].get(target))
            try:
                self.tmpdata[target]['loss'] = int(info.split('=')[1].split('/')[2].split('%')[0])
            except Exception as e:
                logger.error(self.tmpdata[target]['loss'])
                logger.error(e)
            if self.tmpdata[target].get('send_count') == None:
                self.tmpdata[target]['send_count'] = 0
            if self.tmpdata[target].get('warn_startTime') == None:
                self.tmpdata[target]['warn_startTime'] = ''
            if self.tmpdata[target]['loss'] < 100:
                self.tmpdata[target]['max'] = Decimal(info.split('/')[-1]).quantize(Decimal('0.00'))
                self.tmpdata[target]['avg'] = Decimal(info.split('/')[-2]).quantize(Decimal('0.00'))
                self.tmpdata[target]['min'] = Decimal(info.split('/')[-3].split('=')[-1]).quantize(Decimal('0.00'))
            # 写入数据influxdb
            retry = 0
            while retry < cliParam["retry_max"]:
                try:
                    s_host = cliParam["my_name"]
                    s_ip = cliParam["my_public_ip"].replace('.', '_')
                    d_host = self.tmpdata[target]['hostname']
                    d_ip = target.replace('.', '_')
                    loss = int(info.split('=')[1].split('/')[0]) - int(info.split('=')[1].split('/')[1])
                    data = '.'.join([s_host, s_ip, d_host, d_ip])
                    if self.tmpdata[target]['loss'] >= cliParam["send_data_loss_min"]:
                        self.sdc.incr('.'.join(['loss', data]), loss)
                        if self.tmpdata[target]['loss'] < 100:
                            self.sdc.timing('.'.join(['delay.max',data ]), self.tmpdata[target]['max'])
                            self.sdc.timing('.'.join(['delay.min',data ]), self.tmpdata[target]['min'])
                            self.sdc.timing('.'.join(['delay.avg',data ]), self.tmpdata[target]['avg'])
                        logger.info({
                            "s_host": s_host,
                            "s_ip": s_ip,
                            "d_host" : d_host,
                            "d_ip": d_ip,
                            "loss": loss,
                            "data": data
                        })
                        break
                    else:
                        logger.info({
                            "s_host": s_host,
                            "s_ip": s_ip,
                            "d_host" : d_host,
                            "d_ip": d_ip,
                            "loss": loss,
                            "data": data,
                            "info": "not send data to influxdb when cur_loss:%s < send_data_loss_min:%s" % (loss,self.send_data_loss_min)
                        })
                        break
                except Exception as e:
                    logger.error("get fping info err : %s" % e)
                    time.sleep(cliParam["retry_interval"])
                    retry += 1

            # 收集网络数据
            if self.tmpdata[target]['loss'] >= cliParam["warn_loss"]\
                    and self.tmpdata[target]['send_count'] < cliParam["collect_max"] \
                    and cliParam["mtr_status"] == 'true':
                ## 延时发送mtr
                list_mtr_ips.append(target)
            if self.tmpdata[target]['loss'] < cliParam["warn_loss"]:
                self.tmpdata[target]['send_count'] = 0
                self.tmpdata[target]['warn_startTime'] = ''
            if retry > cliParam["retry_max"]:
                logger.error('write influxdb retry count > retry_max')
                sys.exit()
        if list_mtr_ips != []:
            for target in list_mtr_ips:
                collectParam = {
                    "target" : target,
                    "mtr_cmd": cliParam["mtr_cmd"],
                    "ping_cmd": cliParam["ping_cmd"],
                    "fping_cmd": cliParam["fping_cmd"],
                    "my_name": cliParam["my_name"],
                    "my_public_ip": cliParam["my_public_ip"],
                    "mtr_logfile": cliParam["mtr_logfile"],
                    "loss_value": self.tmpdata[target]['loss'],
                    "runtime": runtime,
                    "send_mail_status": cliParam["send_mail_status"],
                    "target_hostname": cliParam["targets_info"].get(target)
                }
                if cliParam.get("admin_list","") != "":
                    collectParam["admin_list"] = cliParam["admin_list"]
                if self.tmpdata[target]['send_count'] < cliParam["collect_max"]:
                    ##
                    self.pool.apply_async(collect_network_data, [collectParam])
                    ##
                    self.tmpdata[target]['send_count'] += 1
                    logger.warn(self.tmpdata[target]['send_count'])
                else:
                    if self.tmpdata[target]['warn_startTime'] == '':
                        self.tmpdata[target]['warn_startTime'] = cur_time
    def run(self):
        while True:
            try:
                self.start()
            except Exception as e:
                logger.error(e)
                sys.exit(2)

def collect_network_data(collectParam):
    try:
        target = collectParam["target"]
        loss_value = collectParam["loss_value"]
        runtime = collectParam["runtime"]
        mtr_cmd = "%s %s" % (collectParam["mtr_cmd"], target)
        ping_cmd = "%s %s" % (collectParam["ping_cmd"], target)
        fping_cmd = collectParam["fping_cmd"]
        hostname = collectParam["target_hostname"]
    except Exception as e:
        print collectParam
        print e

    #正向mtr数据收集
    logger.warn("%s,%s:loss:%s" % (fping_cmd, target, loss_value))
    logger.warn("run: %s" % (mtr_cmd))
    mtr_runtime = str(datetime.datetime.now())
    status, out = commands.getstatusoutput(mtr_cmd)
    local_mtr = out
    print out
    #反向mtr数据收集
    try:
        #获取内网IP
        my_intranet_ip = ""
        I = target.split('.')[0]
        if I == '172' or I == '192' or I == '10':
            for ip in cliParam["local_iplist"]:
                if ip.split('.')[0] == I:
                    my_intranet_ip = ip
        if my_intranet_ip != "":
            test_ip = my_intranet_ip
        else:
            test_ip = collectParam["my_public_ip"]
        url = "http://" + target + cliParam["mtr_apiurl"] + test_ip
        logger.warn("get %s" % (url))
        fails = 0
        print "reverse_mtr"
        req = urllib2.urlopen(url,timeout=60)
        reverse_mtr = req.read()
        #reverse_mtr = urllib2.urlopen(url).read()
        try:
            reverse_mtr = reverse_mtr.replace('<br>', '\n').replace('&nbsp;', ' ')
        except Exception as e:
            logger.error("reverse mtr error : %s" % e)
            local_mtr = ''
    except Exception as e:
        logger.error("get reverse mtr error : %s" % e)
        reverse_mtr = ''
    #正向ping -f 数据收集
    print "fping"
    logger.warn("run: %s" % (ping_cmd))
    ex_ping_runtime = str(datetime.datetime.now())
    status, out = commands.getstatusoutput(ping_cmd)
    ex_ping = out
    print out
    subject = "%s[%s/%s] to %s[%s] loss %d%%" % (collectParam["my_name"], \
                                                collectParam["my_public_ip"],my_intranet_ip,\
                                                target, hostname, loss_value)
    print collectParam["send_mail_status"]
    if collectParam["send_mail_status"] == 'true':
        #send data by email
        try:
            content = '''
            <p>
            <table style="width:1200px;" cellpadding="2" cellspacing="2" align="left" border="1" bordercolor="#CCCCCC">
            <tbody>
            <tr><td colspan="2">%s Run: %s</td></tr>
            <tr><td colspan="2" style="text-align:center;vertical-align:middle;">%s</td></tr>
            <tr><td colspan="2">%s Run: %s</td></tr>
            <tr>
            <td>%s</td>
            <td>%s</td>
            </tr>
            <tr><td colspan="2">%s Run: %s</td></tr>
            <tr><td colspan="2" style="text-align:center;vertical-align:middle;">%s</td></tr>
            </tbody>
            </table><br /></p>
            ''' % (runtime, fping_cmd,
                   subject,
                   mtr_runtime, mtr_cmd,
                   local_mtr.replace('\n','<br>').replace(' ','&nbsp;'),
                   reverse_mtr.replace('\n','<br>').replace(' ','&nbsp;'),
                   ex_ping_runtime, ping_cmd,
                   ex_ping.replace('\n','<br>').replace(' ','&nbsp;'))
        except Exception as e:
            print e
            content = ""
        send_mail(subject, content, collectParam["admin_list"])
    else:
        #save data to local
        with open(collectParam["mtr_logfile"], 'a+') as f:
            f.write("########################\n")
            f.write("%s Run: %s\n" % (runtime, fping_cmd))
            f.write(subject+'\n')
            f.write("%s Run: %s\n" % (mtr_runtime, mtr_cmd))
            f.write(local_mtr+'\n')
            f.write(reverse_mtr+'\n')
            f.write("%s Run: %s\n" % (ex_ping_runtime, ping_cmd))
            f.write(ex_ping+'\n')


def check_format(value):
    return value.replace('"', '').replace("'", '')

def send_mail(subject, content, admin_list):
    print "send_mail...."
    #sendcloud 服务
    api_user = "abc"
    api_key = "YK"
    subject = subject
    fromname = "PingClient"
    params = { "api_user": api_user, \
    "api_key" : api_key,\
    "from" : "PingClient@a.com", \
    "to" : admin_list, \
    "cc" : "",\
    "fromname" : fromname, \
    "subject" : subject, \
    "html": content, \
    "useAddressList" : "true"
    }
    url="https://sendcloud.sohu.com/webapi/mail.send.xml"
    try:
        r = requests.post(url, data=params)
        return True
    except Exception as e:
        logger.error("end mail err : %s" % e)
        return False
def find_local_ip(platform):
    ipstr = '([0-9]{1,3}\.){3}[0-9]{1,3}'
    if platform == "Darwin" or platform == "Linux":
        ipconfig_process = subprocess.Popen("ifconfig", stdout=subprocess.PIPE)
        output = ipconfig_process.stdout.read()
        ip_pattern = re.compile('(inet %s)' % ipstr)
        if platform == "Linux":
            ip_pattern = re.compile('(inet addr:%s)' % ipstr)
        pattern = re.compile(ipstr)
        iplist = []
        for ipaddr in re.finditer(ip_pattern, str(output)):
            ip = pattern.search(ipaddr.group())
            if ip.group() != "127.0.0.1":
                iplist.append(ip.group())
        return iplist
    elif platform == "Windows":
        ipconfig_process = subprocess.Popen("ipconfig", stdout=subprocess.PIPE)
        output = ipconfig_process.stdout.read()
        ip_pattern = re.compile("IPv4 Address(\. )*: %s" % ipstr)
        pattern = re.compile(ipstr)
        iplist = []
        for ipaddr in re.finditer(ip_pattern, str(output)):
            ip = pattern.search(ipaddr.group())
            if ip.group() != "127.0.0.1":
                iplist.append(ip.group())
        return iplist
def initConfig(config):
    config = config
    cliParam = {}
    cliParam["targets_info"] = config['targets_info']
    cliParam["influxdb_config"] = config['influxdb']
    cliParam["main_logfile"] = config['logs'].get('log')
    ##
    ip_list = []
    for key in config.options('targets'):
        ip_list.append(key)
    cliParam["ip_list"] = ip_list
    ##
    local_iplist = find_local_ip(platform.system())
    my_public_ip = config['extend'].get('my_ip')
    if my_public_ip == None:
        url = config.get('extend', 'ipinfo_url')
        my_public_ip = json.loads(urllib2.urlopen(url).read())['data']['ip']
    cliParam["my_public_ip"] = my_public_ip
    cliParam["local_iplist"] = local_iplist
    logger.info("public_ip: %s, local_iplist: %s" % (my_public_ip, local_iplist))
    ##
    my_name = config['extend'].get('my_name')
    if my_name == None:
        my_name = socket.gethostname()
    cliParam["my_name"] = my_name
    ##
    cliParam["fping_cmd"] = check_format(config.get('fping', 'cmd'))
    cliParam["mtr_cmd"] = check_format(config.get('fping', 'mtr_cmd'))
    cliParam["ping_cmd"] = check_format(config.get('fping', 'ping_cmd'))
    cliParam["mtr_status"] = check_format(config.get('fping', 'mtr'))
    cliParam["mtr_logfile"] = check_format(config.get('fping', 'log'))
    cliParam["mtr_apiurl"] = config.get('extend', 'mtr_apiurl').replace(' ', '')
    logger.info('mtr_logfile: %s' % cliParam["mtr_logfile"])
    ##
    cliParam["warn_loss"] = config.getint('fping', 'warn_loss')
    cliParam["collect_max"] = config.getint('fping', 'collect_max')
    cliParam["retry_max"] = config.getint('extend', 'retry_max')
    cliParam["retry_interval"] = config.getint('extend', 'retry_interval')
    cliParam["fping_retry_max"] = config.getint('extend', 'fping_retry_max')
    cliParam["fping_retry_interval"] = config.getint('extend', 'fping_retry_interval')
    ##
    send_mail_status = check_format(config.get('extend', 'send_mail'))
    if send_mail_status == 'true':
        admin_list = config.get('extend', 'admin_list')
        cliParam["admin_list"] = admin_list
    cliParam["send_mail_status"] = send_mail_status
    cliParam["send_data_loss_min"] = int(config['extend'].get('send_data_loss_min', 11))
    return cliParam
if __name__ == '__main__':
    config_file = parse_config(sys.argv[1:])
    logger = logging.getLogger()

    config = configparser.ConfigParser()
    config.read(config_file.decode())

    cliParam = initConfig(config)
    logger = set_log(logger=logger, log_file=cliParam["main_logfile"])

    monitor = Monitor(cliParam)
    monitor.run()


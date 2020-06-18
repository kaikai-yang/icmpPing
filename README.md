### icmp_client

*概述*

```
本程序是丢包检测程序,功能是测试本机到其他ip的网络稳定性，监控丢包。监控程序会检测实时通过fping检测目标主机，并记录丢包状况，丢包数据会实时写入influxdb（由于之前量级并发上万,直接写入influxdb无法承载，所以解决是通过先发送到statsd进行聚合）。同时会开启一个http-server提供api，该server是最简单的flask框架,用作反向收集网络信息，程序会将实时的mtr记录到/tmp/mtr.log文件中，方便当时状态查询
```

*应用场景*
```
对网络波动率要求特别高，需要实时反馈网络状态，类似somkeping，但相比更灵活。且把反馈运营商时需要的ping , mtr , 反向mtr, 信息全部收集。达到高效沟通，快速定位的目的。
```

*安装*

```
a 安装fping cp /usr/local/icmpPing/sbin/fping /usr/bin/fping 

b 安装mtr yum install mtr -y

a (python2) pip install -r /usr/local/icmpPing/requirements.txt

b 刷新supervisor配置文件,将/usr/local/icmpPing/init/icmpPing.ini 更新到supervisor icmpPing.ini文件中 
```

*启动方式*


```
a supervisor 
本程序使用supervisor管理supervisor 管理启动 supervisorctl restart/status/stop/start icmp_clientd

b 直接启动
/usr/local/icmpPing/src/client.py -c /usr/local/icmpPing/conf/client.ini
```



**fping**
```
fping -B 1 -D -b 200 -O 0 -c 10 -p 1000 -q
-B 当发起多个请求时，到下一个发送还没收到则不等待，
-D 添加时间戳
-b 包大小
-O
-c ping n个包
-p 每个包间隔
-q 只输出结果
```

期待改进意见: yangkainn@163.com

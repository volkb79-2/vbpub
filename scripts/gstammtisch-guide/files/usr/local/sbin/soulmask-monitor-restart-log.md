# From stopping server into starting server 

you see nicely 
- server disappears
- server appears
- boot phase cgroup memory.min=8G (limited by parent cgroup?)
  - wings egg set for startup phase: WINGS_CG_STARTUP_MEMORY_MIN=9G, WINGS_CG_STARTUP_MEMORY_LOW=9G
  - wings egg set for steady phase: WINGS_CG_MEMORY_MIN=4500M, WINGS_CG_MEMORY_LOW=6G
- early move into zpool
- 

## Environemnt 

- wings Daemon Version	1.13.1-cgroup.11 (Latest: 1.13.3) - custom patched version (`image: wings-local:1.13.1-cgroup.11`)
  - see `wings-cgroups/v1-legacy`

## Issues TBD

- script should only monitor, not adapt cgroup drift by itself (or who does?) - it is interfering with cgroups limits being applied by wings
- keep showing `S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0` while after a script restart it changed to `S1 (MAIN): min=4500M low=6G high=6G max=20G cpu=2000 io=default 800 KSM:m=24179z=2742+58M `

## Output

```txt
[monitor] Soulmask cgroup disappeared for b87c0a5b-2387-4a1c-8863-ff23e6800a1d (container removed/restarted?) — dropping from monitor
[monitor] all Soulmask servers gone — waiting for one to (re)appear...
[monitor] waiting for Soulmask container (WSServer-Linux-Shipping)... Ctrl-C to abort
[monitor] found Soulmask server(s): b87c0a5b-2387-4a1c-8863-ff23e6800a1d (/sys/fs/cgroup/wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice)
  time   |        S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0         |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
00:46:16 | 838M   786M    0M       —        —        —        —        0        0       0B        —      | —        —       —       | 2208M  0M     81M     0/s      1210/s   | 3M     0M     0M      0/s      0/s      | 1499M
00:46:21 | 941M   889M    0M       —        0/s      0/s      0/s      0        0       0B        —      | 0.0/s    28/s    0/s     | 2208M  0M     81M     0/s      0/s      | 3M     0M     0M      0/s      0/s      | 1499M
00:46:26 | 947M   896M    0M       —        0/s      0/s      0/s      0        0       0B        —      | 0.2/s    24/s    3/s     | 2208M  0M     81M     0/s      0/s      | 3M     0M     0M      0/s      0/s      | 1499M
00:46:31 | 954M   902M    0M       —        0/s      0/s      0/s      0        0       0B        —      | 0.0/s    13/s    0/s     | 2208M  0M     81M     0/s      0/s      | 3M     0M     0M      0/s      0/s      | 1499M
00:46:36 | 945M   892M    0M       —        0/s      0/s      0/s      0        0       0B        —      | 0.0/s    24/s    0/s     | 2208M  0M     81M     0/s      0/s      | 3M     0M     0M      0/s      0/s      | 1499M
00:46:41 | 1309M  1256M   0M       —        0/s      0/s      0/s      0        0       0B        —      | 0.0/s    24/s    0/s     | 2208M  0M     81M     0/s      0/s      | 3M     0M     0M      0/s      0/s      | 1499M
00:46:49 | 2745M  2687M   0M       —        0/s      0/s      0/s      0        0       -686.8K   —      | 0.0/s    176/s   0/s     | 1970M  0M     319M    0/s      34/s     | 3M     0M     0M      0/s      0/s      | 1740M
00:46:54 | 3449M  3390M   0M       —        0/s      0/s      0/s      0        0       -5.9M     —      | 0.0/s    4/s     0/s     | 1640M  0M     649M    0/s      28/s     | 3M     0M     0M      0/s      0/s      | 2073M
  time   |        S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0         |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
00:46:59 | 3831M  3770M   0M       —        0/s      0/s      0/s      0        0       -10.8M    —      | 0.0/s    10/s    0/s     | 1340M  0M     950M    0/s      387/s    | 3M     0M     0M      0/s      0/s      | 2383M
00:47:04 | 4341M  4309M   0M       —        0/s      0/s      0/s      0        0       -15.7M    —      | 0.0/s    233/s   0/s     | 1061M  0M     1228M   0/s      74/s     | 3M     0M     0M      0/s      0/s      | 2670M
00:47:09 | 4885M  4852M   0M       —        0/s      0/s      0/s      0        0       -20.7M    —      | 0.0/s    5/s     0/s     | 1046M  0M     1243M   0/s      75/s     | 3M     0M     0M      0/s      0/s      | 2694M
00:47:14 | 5194M  5127M   47M      3.79x    51/s     0/s      0/s      0        0       -25.7M    —      | 0.0/s    10/s    1/s     | 1018M  0M     1271M   0/s      36/s     | 3M     0M     0M      0/s      0/s      | 2745M
00:47:26 | 5838M  5565M   253M     2.45x    1726/s   0/s      1/s      0        0       -36.5M    —      | 0.0/s    32/s    0/s     | 592M   0M     1697M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3263M
00:47:31 | 5795M  5371M   404M     3.22x    1369/s   0/s      0/s      0        0       -40.2M    —      | 0.0/s    6/s     18/s    | 573M   0M     1716M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3276M
00:47:36 | 5675M  5180M   474M     3.41x    15316/s  0/s      2/s      0        0       -40.2M    —      | 0.0/s    19/s    0/s     | 505M   0M     1784M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3358M
00:47:41 | 5638M  5155M   463M     3.55x    5013/s   0/s      0/s      0        0       -40.2M    —      | 0.2/s    17/s    10/s    | 500M   0M     1789M   0/s      1/s      | 3M     0M     0M      0/s      0/s      | 3352M
00:47:46 | 5702M  5175M   506M     3.67x    10603/s  3/s      0/s      0        3       -43.1M    —      | 0.0/s    184/s   1/s     | 483M   0M     1806M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3381M
00:48:02 | 5961M  5179M   759M     3.21x    16485/s  2/s      0/s      0        3       -60.8M    —      | 0.0/s    28/s    10/s    | 455M   0M     1833M   0/s      7051/s   | 3M     0M     0M      0/s      0/s      | 3437M
00:48:07 | 5600M  4649M   924M     3.70x    571/s    0/s      0/s      0        3       -77.4M    —      | 0.0/s    4/s     0/s     | 455M   0M     1833M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3444M
00:48:12 | 5745M  4771M   947M     3.78x    1635/s   0/s      0/s      0        3       -85.4M    —      | 0.0/s    4/s     7/s     | 484M   0M     1805M   0/s      2097/s   | 3M     0M     0M      0/s      0/s      | 3424M
00:48:17 | 5661M  4548M   1084M    3.85x    229/s    0/s      0/s      0        91      -87.3M    —      | 0.0/s    10/s    26/s    | 511M   0M     1778M   0/s      2462/s   | 3M     0M     0M      0/s      0/s      | 3405M
00:48:34 | 5339M  3989M   1321M    4.09x    2715/s   0/s      0/s      63       91      -68.3M    —      | 0.2/s    43/s    44/s    | 410M   0M     1879M   0/s      5195/s   | 3M     0M     0M      0/s      0/s      | 3515M
00:48:39 | 4951M  3419M   1506M    4.30x    3565/s   0/s      1/s      63       91      -68.3M    —      | 0.0/s    10/s    3/s     | 386M   0M     1903M   0/s      123/s    | 3M     0M     0M      0/s      0/s      | 3545M
00:48:44 | 4852M  3291M   1536M    4.31x    1777/s   0/s      0/s      63       91      -76.2M    —      | 0.0/s    6/s     3/s     | 374M   0M     1914M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3551M
00:48:49 | 4794M  3192M   1576M    4.29x    74/s     0/s      0/s      63       91      -92.3M    —      | 0.0/s    5/s     2/s     | 366M   0M     1923M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3550M
00:49:02 | 4446M  2760M   1662M    4.29x    362/s    0/s      0/s      21101    451     +34.3M    —      | 0.2/s    43/s    13/s    | 345M   0M     1944M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3565M
00:49:07 | 4668M  2994M   1649M    4.22x    6239/s   0/s      1/s      22059    454     +39.2M    —      | 0.0/s    20/s    1/s     | 289M   0M     2000M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3620M
00:49:12 | 5007M  3356M   1626M    4.07x    35179/s  1/s      0/s      22017    444     +39.0M    —      | 0.0/s    136/s   1/s     | 283M   0M     2006M   0/s      10/s     | 3M     0M     0M      0/s      0/s      | 3629M
00:49:17 | 5352M  3684M   1643M    3.91x    27389/s  0/s      0/s      22011    442     +39.0M    —      | 0.0/s    42/s    12/s    | 271M   0M     2017M   0/s      6/s      | 3M     0M     0M      0/s      0/s      | 3642M
00:49:22 | 5650M  3986M   1637M    3.83x    23388/s  2313/s   0/s      22217    541     +41.1M    —      | 0.0/s    5/s     2/s     | 229M   0M     2060M   0/s      2/s      | 3M     0M     0M      0/s      0/s      | 3657M
00:49:34 | 5786M  4119M   1641M    3.83x    7947/s   192/s    0/s      23682    687     +52.7M    —      | 0.0/s    25/s    8/s     | 226M   0M     2063M   0/s      11/s     | 3M     0M     0M      0/s      0/s      | 3650M
00:49:39 | 6686M  5103M   1556M    3.43x    26438/s  0/s      0/s      23261    934     +47.3M    —      | 0.0/s    65/s    30/s    | 159M   0M     2130M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3739M
00:49:44 | 6639M  4975M   1637M    3.27x    31101/s  8/s      0/s      22882    938     +37.4M    —      | 0.0/s    12/s    10/s    | 158M   0M     2131M   0/s      4/s      | 3M     0M     0M      0/s      0/s      | 3747M
00:49:49 | 6442M  4804M   1610M    2.89x    44665/s  32/s     0/s      22463    896     +26.7M    —      | 0.0/s    74/s    1/s     | 158M   0M     2130M   0/s      44/s     | 3M     0M     0M      0/s      0/s      | 3749M
[monitor] [b87c0a5b-2387-4a1c-8863-ff23e6800a1d] cgroup drift on /sys/fs/cgroup/wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice:
[monitor]   changed: memory.min 8G -> 4500M; memory.low 9G -> 6G; memory.high 15G -> 6482M
[monitor]   current: memory.min=4500M memory.low=6G memory.high=6482M memory.max=20G cpu.weight=2000 io.bfq.weight=default 800 memory.zswap.writeback=1
00:49:54 | 6430M  4792M   1610M    2.90x    8176/s   0/s      0/s      22059    893     +16.8M    —      | 0.0/s    16/s    10/s    | 169M   0M     2120M   0/s      532/s    | 3M     0M     0M      0/s      0/s      | 3736M
[monitor] [b87c0a5b-2387-4a1c-8863-ff23e6800a1d] cgroup drift on /sys/fs/cgroup/wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice:
[monitor]   changed: memory.high 6482M -> 6G
[monitor]   current: memory.min=4500M memory.low=6G memory.high=6G memory.max=20G cpu.weight=2000 io.bfq.weight=default 800 memory.zswap.writeback=1
00:50:07 | 6105M  4349M   1721M    2.97x    10209/s  66/s     21/s     8370     898     -41.4M    —      | 0.2/s    87/s    15/s    | 169M   0M     2120M   0/s      1/s      | 3M     0M     0M      0/s      0/s      | 3739M
00:50:12 | 6106M  4323M   1747M    2.97x    170/s    1/s      0/s      8725     897     -39.9M    —      | 0.0/s    2/s     0/s     | 169M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3737M
00:50:17 | 6105M  4329M   1741M    2.96x    1082/s   43/s     0/s      8766     968     -33.9M    —      | 0.0/s    19/s    0/s     | 169M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3730M
00:50:22 | 6102M  4322M   1745M    2.98x    18/s     0/s      0/s      10041    1058    -26.7M    —      | 0.0/s    5/s     1/s     | 169M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3729M
00:50:27 | 6064M  4284M   1745M    2.98x    1178/s   1/s      0/s      10951    926     -22.9M    —      | 0.0/s    186/s   1/s     | 169M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3728M
00:50:39 | 6033M  4265M   1733M    2.98x    3202/s   6/s      0/s      12633    924     -15.7M    —      | 0.0/s    86/s    22/s    | 170M   0M     2119M   0/s      82/s     | 3M     0M     0M      0/s      0/s      | 3717M
00:50:44 | 6049M  4279M   1735M    2.99x    44/s     0/s      0/s      15645    1751    -288.9K   —      | 0.0/s    4/s     2/s     | 169M   0M     2119M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3717M
00:50:49 | 6069M  4297M   1737M    2.99x    816/s    0/s      0/s      15431    1557    -1.9M     —      | 0.2/s    175/s   1/s     | 168M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3718M
00:50:54 | 6100M  4328M   1737M    2.99x    3/s      0/s      0/s      15429    1557    -1.9M     —      | 0.0/s    12/s    5/s     | 168M   0M     2120M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3714M
00:50:59 | 6038M  4267M   1741M    2.99x    409/s    0/s      15/s     15388    1557    -2.0M     —      | 0.0/s    17/s    0/s     | 167M   0M     2122M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3711M
00:51:12 | 6058M  4288M   1741M    2.99x    84/s     0/s      0/s      16287    4895    +14.2M    —      | 0.0/s    495/s   2/s     | 167M   0M     2122M   0/s      70/s     | 3M     0M     0M      0/s      0/s      | 3696M
00:51:17 | 5993M  4245M   1718M    2.96x    734/s    5/s      2/s      16204    5949    +18.6M    —      | 0.0/s    10/s    0/s     | 164M   0M     2125M   0/s      2/s      | 3M     0M     0M      0/s      0/s      | 3696M
00:51:22 | 5996M  4250M   1717M    2.96x    230/s    2/s      0/s      17500    5718    +22.8M    —      | 0.0/s    10/s    2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3707M
  time   |        S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0         |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
00:51:27 | 5945M  4182M   1731M    2.98x    388/s    2/s      0/s      17909    5650    +24.1M    —      | 0.0/s    324/s   1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3708M
00:51:32 | 5957M  4189M   1734M    2.98x    30/s     0/s      0/s      17992    5645    +24.7M    —      | 0.0/s    76/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3707M
00:51:44 | 5981M  4218M   1734M    2.98x    149/s    0/s      0/s      18234    5171    +23.9M    —      | 0.0/s    36/s    5/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3690M
00:51:49 | 6030M  4256M   1700M    3.01x    1271/s   0/s      365/s    18414    5171    +25.0M    —      | 0.0/s    74/s    2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3687M
00:51:54 | 5995M  4232M   1700M    3.01x    33/s     0/s      0/s      18407    5170    +25.1M    —      | 0.0/s    48/s    2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3686M
00:51:59 | 5993M  4256M   1701M    3.01x    23/s     0/s      0/s      18598    5720    +28.0M    —      | 0.0/s    17/s    17/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3684M
00:52:04 | 5949M  4208M   1706M    3.02x    36/s     0/s      0/s      18737    5527    +27.8M    —      | 0.0/s    26/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3683M
00:52:18 | 5962M  4219M   1708M    3.02x    39/s     0/s      0/s      18694    5502    +27.5M    —      | 0.2/s    184/s   2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3682M
00:52:23 | 5912M  4157M   1716M    3.04x    216/s    2/s      0/s      18340    5482    +26.1M    —      | 0.0/s    30/s    0/s     | 150M   0M     2139M   0/s      8/s      | 3M     0M     0M      0/s      0/s      | 3683M
00:52:28 | 5859M  4098M   1725M    3.06x    312/s    0/s      0/s      17889    5479    +23.5M    —      | 0.0/s    19/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3685M
00:52:33 | 5790M  4011M   1743M    3.07x    122/s    0/s      0/s      17906    5477    +23.9M    —      | 0.0/s    15/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3685M
00:52:38 | 5755M  3964M   1754M    3.10x    66/s     0/s      0/s      17914    5577    +24.4M    —      | 0.0/s    9/s     1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3688M
00:52:46 | 5734M  3947M   1752M    3.09x    401/s    0/s      0/s      17861    5617    +25.3M    0.3    | 0.0/s    112/s   1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3690M
00:52:51 | 5904M  4144M   1711M    3.12x    2726/s   365/s    20/s     17603    2549    +12.5M    3.9    | 0.0/s    41/s    0/s     | 149M   0M     2139M   0/s      2/s      | 3M     0M     0M      0/s      0/s      | 3680M
00:52:56 | 5927M  4173M   1704M    3.10x    2814/s   3/s      0/s      17342    2555    +11.7M    7.7    | 0.0/s    8/s     3/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3679M
00:53:01 | 5956M  4197M   1702M    3.10x    489/s    2/s      0/s      17327    2555    +12.8M    12.4   | 0.0/s    8/s     0/s     | 150M   0M     2139M   0/s      1/s      | 3M     0M     0M      0/s      0/s      | 3679M
00:53:06 | 5967M  4208M   1701M    3.10x    244/s    2/s      0/s      17578    2555    +13.9M    17.1   | 0.0/s    84/s    1/s     | 150M   0M     2139M   0/s      3/s      | 3M     0M     0M      0/s      0/s      | 3678M
00:53:11 | 5972M  4208M   1701M    3.10x    55/s     0/s      0/s      17587    2556    +14.1M    21.9   | 0.0/s    6/s     2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3677M
00:53:19 | 5947M  4173M   1707M    3.10x    131/s    0/s      0/s      17585    2558    +14.5M    26.6   | 0.0/s    12/s    53/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3672M
00:53:24 | 5946M  4173M   1706M    3.10x    230/s    0/s      0/s      17583    2557    +14.8M    28.1   | 0.0/s    7/s     1/s     | 150M   0M     2139M   0/s      1/s      | 3M     0M     0M      0/s      0/s      | 3673M
00:53:29 | 5929M  4154M   1708M    3.10x    75/s     0/s      0/s      17579    2594    +14.9M    28.5   | 0.0/s    10/s    11/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3672M
00:53:34 | 5874M  4043M   1752M    3.08x    244/s    26/s     0/s      17534    2595    +14.8M    28.4   | 0.0/s    68/s    11/s    | 150M   0M     2139M   0/s      1/s      | 3M     0M     0M      0/s      0/s      | 3678M
00:53:39 | 5876M  4046M   1752M    3.08x    80/s     0/s      0/s      17575    2598    +15.0M    28.5   | 0.0/s    57/s    62/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3633M
00:53:44 | 5878M  4043M   1751M    3.08x    134/s    0/s      275/s    17575    2598    +15.0M    28.7   | 0.2/s    14/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3628M
00:53:51 | 5842M  4024M   1752M    3.08x    103/s    0/s      23/s     17563    2598    +15.0M    29.0   | 0.0/s    43/s    13/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3626M
00:53:56 | 5839M  4006M   1757M    3.09x    100/s    0/s      0/s      17501    2595    +14.7M    29.1   | 0.0/s    9/s     12/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3617M
00:54:01 | 5816M  3963M   1763M    3.09x    45/s     0/s      0/s      16217    2595    +10.7M    29.2   | 0.0/s    337/s   10/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:54:06 | 5739M  3848M   1801M    3.09x    60/s     0/s      0/s      15773    2595    +8.7M     29.3   | 0.0/s    67/s    9/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:54:11 | 5630M  3702M   1838M    3.11x    374/s    1/s      0/s      15630    2608    +8.8M     29.3   | 0.0/s    10/s    2/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3617M
00:54:16 | 5636M  3710M   1836M    3.11x    391/s    0/s      0/s      15341    2610    +8.9M     29.3   | 0.0/s    12/s    0/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3614M
00:54:24 | 5631M  3706M   1835M    3.11x    142/s    0/s      0/s      15326    2698    +9.8M     29.4   | 0.2/s    60/s    21/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:54:29 | 5631M  3707M   1835M    3.11x    35/s     0/s      0/s      15326    2698    +9.8M     29.4   | 0.0/s    8/s     5/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:54:34 | 5638M  3716M   1833M    3.11x    234/s    0/s      0/s      15326    2698    +9.8M     29.3   | 0.0/s    40/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:54:39 | 5644M  3722M   1833M    3.10x    142/s    0/s      4/s      15324    2698    +9.8M     29.5   | 0.0/s    42/s    0/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:54:44 | 5641M  3720M   1832M    3.10x    39/s     0/s      0/s      15311    2698    +9.8M     29.5   | 0.0/s    11/s    0/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:54:49 | 5636M  3716M   1832M    3.10x    74/s     0/s      0/s      16338    2973    +16.3M    29.6   | 0.0/s    25/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:54:57 | 5637M  3717M   1831M    3.10x    144/s    0/s      0/s      17308    2705    +19.3M    29.6   | 0.0/s    38/s    21/s    | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3617M
00:55:02 | 5602M  3685M   1836M    3.11x    23/s     0/s      10/s     17432    2705    +19.8M    29.6   | 0.0/s    7/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3615M
00:55:07 | 5556M  3633M   1843M    3.13x    63/s     0/s      11/s     17999    2705    +22.0M    29.7   | 0.0/s    14/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:55:12 | 5558M  3635M   1843M    3.13x    26/s     1/s      18/s     18317    2705    +23.4M    29.6   | 0.0/s    15/s    2/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
  time   |        S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0         |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
00:55:17 | 5510M  3580M   1851M    3.14x    11/s     0/s      0/s      18760    2705    +25.4M    29.5   | 0.0/s    36/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:55:22 | 5497M  3565M   1853M    3.15x    17/s     0/s      0/s      19687    2710    +29.2M    29.5   | 0.0/s    68/s    27/s    | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:55:29 | 5426M  3486M   1864M    3.17x    34/s     0/s      0/s      19893    2710    +30.0M    29.4   | 0.0/s    25/s    6/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:55:34 | 5423M  3481M   1865M    3.17x    22/s     0/s      0/s      19877    2710    +30.0M    29.4   | 0.1/s    13/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:55:39 | 5408M  3456M   1869M    3.18x    11/s     0/s      398/s    19731    2710    +29.4M    29.4   | 0.0/s    9/s     4/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:55:44 | 5376M  3418M   1876M    3.19x    78/s     0/s      0/s      19699    2706    +29.3M    29.6   | 0.0/s    27/s    31/s    | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:55:49 | 5369M  3411M   1876M    3.19x    10/s     0/s      0/s      19675    2706    +29.2M    29.6   | 0.0/s    22/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:55:54 | 5374M  3416M   1876M    3.19x    25/s     0/s      0/s      19653    2706    +29.1M    29.8   | 0.0/s    8/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:02 | 5325M  3361M   1883M    3.21x    21/s     0/s      0/s      19653    2706    +29.1M    29.7   | 0.0/s    35/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:07 | 5318M  3354M   1882M    3.21x    41/s     0/s      0/s      19651    2706    +29.1M    29.7   | 0.0/s    12/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:12 | 5320M  3357M   1881M    3.21x    223/s    0/s      0/s      20120    2706    +31.0M    29.7   | 0.0/s    7/s     0/s     | 149M   0M     2139M   0/s      3/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:17 | 5326M  3364M   1880M    3.21x    103/s    0/s      0/s      20118    2706    +31.2M    29.7   | 0.0/s    136/s   0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:22 | 5325M  3363M   1880M    3.21x    5/s      0/s      0/s      20396    2711    +33.3M    29.6   | 0.0/s    17/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3610M
00:56:27 | 5318M  3355M   1881M    3.21x    20/s     0/s      0/s      20607    2711    +35.2M    29.7   | 0.0/s    37/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:35 | 5195M  3204M   1911M    3.24x    90/s     0/s      0/s      22703    2720    +46.1M    29.6   | 0.0/s    274/s   0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:56:40 | 5184M  3190M   1914M    3.25x    6/s      0/s      0/s      22447    2717    +45.1M    29.5   | 0.1/s    36/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:56:45 | 5182M  3188M   1914M    3.25x    13/s     0/s      0/s      22421    2717    +45.0M    29.4   | 0.0/s    65/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:56:50 | 5152M  3124M   1902M    3.30x    2874/s   0/s      2056/s   22376    2717    +44.8M    29.4   | 0.0/s    49/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3614M
00:56:55 | 5156M  3127M   1902M    3.30x    8/s      0/s      0/s      22356    2717    +44.7M    29.4   | 0.0/s    29/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3614M
00:57:00 | 5150M  3122M   1902M    3.30x    12/s     0/s      0/s      22341    2717    +44.7M    29.3   | 0.0/s    83/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3614M
00:57:07 | 5134M  3104M   1905M    3.30x    23/s     0/s      0/s      22321    2717    +44.6M    29.4   | 0.0/s    105/s   50/s    | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:57:12 | 5136M  3106M   1905M    3.31x    11/s     0/s      0/s      22317    2715    +44.6M    29.6   | 0.0/s    28/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:57:17 | 5100M  3064M   1912M    3.32x    12/s     0/s      0/s      22313    2713    +44.6M    29.5   | 0.0/s    23/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:57:22 | 5103M  3067M   1911M    3.32x    142/s    0/s      0/s      22307    2708    +44.1M    29.6   | 0.0/s    30/s    1/s     | 150M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3613M
00:57:27 | 5073M  3034M   1915M    3.32x    103/s    1/s      0/s      22630    2712    +44.8M    29.5   | 0.0/s    160/s   61/s    | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3612M
00:57:32 | 5076M  3037M   1915M    3.32x    19/s     0/s      0/s      22695    2720    +46.8M    29.5   | 0.0/s    33/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3611M
00:57:40 | 5060M  3019M   1918M    3.33x    50/s     0/s      0/s      22549    2725    +50.1M    29.3   | 0.0/s    72/s    2/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3610M
00:57:45 | 5052M  3012M   1918M    3.33x    41/s     0/s      0/s      22544    2721    +50.1M    29.3   | 0.0/s    13/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3610M
00:57:50 | 5054M  3012M   1918M    3.33x    3/s      0/s      0/s      22542    2721    +50.1M    29.3   | 0.2/s    12/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3609M
00:57:55 | 5067M  3012M   1918M    3.33x    10/s     0/s      702/s    22542    2721    +50.1M    29.4   | 0.0/s    10/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3609M
00:58:00 | 5080M  3017M   1917M    3.33x    11/s     0/s      405/s    22542    2721    +50.1M    29.4   | 0.0/s    28/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:05 | 5072M  3010M   1917M    3.33x    22/s     0/s      0/s      22542    2721    +50.1M    29.5   | 0.0/s    10/s    1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:12 | 5072M  3010M   1917M    3.33x    11/s     0/s      0/s      22540    2721    +50.1M    29.8   | 0.0/s    27/s    20/s    | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:17 | 5080M  3017M   1917M    3.33x    18/s     0/s      0/s      21759    2722    +47.1M    29.9   | 0.0/s    22/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:22 | 5077M  3015M   1917M    3.33x    15/s     0/s      0/s      21705    2722    +46.9M    29.8   | 0.0/s    24/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:27 | 5084M  3022M   1917M    3.33x    58/s     0/s      0/s      21687    2722    +46.8M    29.6   | 0.0/s    29/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3608M
00:58:32 | 5079M  3018M   1917M    3.33x    16/s     0/s      0/s      21732    2722    +47.0M    29.7   | 0.0/s    66/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:58:37 | 5107M  3065M   1898M    3.35x    1350/s   0/s      0/s      21833    2726    +47.5M    29.5   | 0.0/s    15/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:58:44 | 5087M  3045M   1898M    3.35x    8/s      0/s      0/s      21985    2726    +48.1M    29.4   | 0.0/s    24/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:58:49 | 5088M  3045M   1898M    3.35x    0/s      0/s      0/s      22049    2728    +48.4M    29.4   | 0.0/s    6/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
  time   |        S1 (MAIN): min=8G low=9G high=15G max=20G cpu=2000 io=default 800 KSM:m=0z=0+0         |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
00:58:54 | 5086M  3043M   1898M    3.35x    2/s      0/s      0/s      22209    2728    +49.0M    29.5   | 0.0/s    12/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:58:59 | 5084M  3042M   1898M    3.35x    4/s      0/s      0/s      22390    2728    +49.7M    29.6   | 0.0/s    7/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:04 | 5085M  3042M   1898M    3.35x    22/s     0/s      0/s      22583    2734    +50.6M    29.7   | 0.0/s    15/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:09 | 5084M  3042M   1898M    3.35x    9/s      0/s      0/s      22629    2731    +50.8M    29.8   | 0.0/s    9/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:16 | 5089M  3046M   1897M    3.35x    18/s     0/s      0/s      22689    2727    +51.0M    29.7   | 0.0/s    56/s    2/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:21 | 5094M  3052M   1897M    3.35x    17/s     0/s      0/s      22689    2725    +51.0M    29.7   | 0.0/s    12/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:26 | 5086M  3044M   1897M    3.35x    7/s      0/s      0/s      22681    2725    +51.0M    29.7   | 0.2/s    9/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:31 | 5089M  3047M   1897M    3.35x    24/s     0/s      0/s      22681    2725    +51.0M    29.7   | 0.0/s    6/s     0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:36 | 5080M  3037M   1898M    3.35x    22/s     0/s      0/s      22679    2725    +51.0M    29.5   | 0.0/s    10/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3606M
00:59:41 | 5079M  3036M   1899M    3.35x    7/s      0/s      0/s      22673    2725    +51.0M    29.5   | 0.0/s    9/s     1/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3607M
00:59:48 | 5078M  3034M   1899M    3.35x    31/s     0/s      0/s      22671    2725    +51.0M    29.6   | 0.0/s    41/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3606M
00:59:53 | 5073M  3029M   1899M    3.35x    0/s      0/s      0/s      22671    2725    +51.0M    29.6   | 0.0/s    14/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3606M
00:59:58 | 5045M  2997M   1903M    3.36x    3/s      0/s      0/s      23867    2725    +55.1M    29.6   | 0.0/s    127/s   0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3604M
01:00:03 | 5046M  2998M   1903M    3.36x    2/s      0/s      0/s      24173    2725    +56.4M    29.7   | 0.0/s    189/s   0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3603M
01:00:08 | 5030M  2979M   1907M    3.37x    9/s      0/s      0/s      24199    2740    +56.5M    29.7   | 0.0/s    12/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3603M
^C
[monitor] stopped.
root@gstammtisch:/home/vb/volkb79-2/vbpub/.worktrees/debian-install-update/scripts/gstammtisch-guide/files/usr/local/sbin# ./soulmask-monitor.sh
Soulmask memory monitor — Ctrl-C to stop   (interval: 5s)

  wings.slice   min=8G high=14G writeback=1

  SERVER 1: UUID b87c0a5b-2387-4a1c-8863-ff23e6800a1d
    container: bfd85ac1c2a0 (b87c0a5b-2387-4a1c-8863-ff23e6800a1d)
    slice:     /sys/fs/cgroup/wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice (MAIN)
    applied:   memory.min=4500M memory.low=6G memory.high=6G memory.max=20G cpu.weight=2000 io.bfq.weight=default 800 memory.zswap.writeback=1
    KSM:       pid=132516 status=on merge_any=yes mergeable=yes merging=24179p zero=2742p rmap=780736 profit=+57.5M
  KSM host:   run=1 advisor=none [scan-time] zero_pages=1 shared=7297 sharing=38839 ksm_zero=279218 profit=+1.1G scanned=3682353793 scan/s=— full_scans=8690 full/s=— cow=11865101 cow/s=— swpin_copy=414492 swpin/s=—
  KSM suggestions:
    - KSM COW events are non-zero; watch their rate because writes to merged pages pay a copy cost.
    - KSM swap-in copies are non-zero; correlate with disk refaults before increasing KSM scope.
  TMPFS parent (soulmask_tmpfs.slice)   min=800M
    ZSwapMax0 (pak)   min=150M high=max writeback=1  (writeback=1 - cold pages MAY be written through to real disk under pressure)
    ZSwapMax1 (compressible)   min=30M high=max writeback=1  (writeback=1 - cold pages MAY be written through to real disk under pressure)

  Applied server cgroup controls above are re-read every sample; a note is
  printed on stderr only when they drift.  ('file' column: --wide or --json.)

  time   |   S1 (MAIN): min=4500M low=6G high=6G max=20G cpu=2000 io=default 800 KSM:m=24179z=2742+58M   |         KSM host         |            T0 (pak) min=150M            |            T1 (cmpr) min=30M            |  swap
time     | RAM    anon    zpool    ratio    rfz/s    rfd/s    rff/s    merge    zero    profit    fps    | Kfull/s  Kcow/s  Kswp/s  | T0_RAM T0_z   T0_disk T0_rfz/s T0_rfd/s | T1_RAM T1_z   T1_disk T1_rfz/s T1_rfd/s | disk_sw
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
01:00:16 | 4937M  2869M   1925M    3.39x    —        —        —        24179    2742    +57.5M    29.6   | —        —       —       | 149M   0M     2139M   —        —        | 3M     0M     0M      —        —        | 3604M
01:00:21 | 4791M  2700M   1951M    3.44x    10/s     0/s      0/s      24140    2745    +57.6M    29.5   | 0.0/s    23/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3604M
01:00:26 | 4703M  2585M   1978M    3.45x    4/s      0/s      0/s      24125    2745    +57.6M    29.5   | 0.0/s    15/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3604M
01:00:31 | 4622M  2482M   1999M    3.46x    26/s     0/s      0/s      24088    2745    +57.4M    29.5   | 0.2/s    28/s    0/s     | 149M   0M     2139M   0/s      0/s      | 3M     0M     0M      0/s      0/s      | 3604M

```


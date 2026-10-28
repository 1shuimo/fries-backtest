"""Read-only import of the user's watchlist; snapshot fields are not signals."""
import argparse
import csv
import json
from pathlib import Path
from sector_b1 import SECTOR

SOURCE=Path('data/watchlist.csv')
ROOT=Path('data/watchlist_20260917')
INDUSTRIES={
    '半导体':'半导体及设备','半导体设备与材料':'半导体及设备',
    '生物技术':'医疗健康','一般药品制造商':'医疗健康','专业与通用药品制造商':'医疗健康',
    '健康信息服务':'医疗健康','诊断与研究':'医疗健康',
    '信贷':'金融','资本市场':'金融','多元化银行':'金融','财产和意外伤害保险':'金融',
    '通讯设备':'硬件及网络设备','计算机硬件':'硬件及网络设备','消费电子产品':'硬件及网络设备',
    '电子元件':'硬件及网络设备','科技仪器':'硬件及网络设备',
    '应用软件':'软件云及安全','软件基础设施':'软件云及安全','信息技术服务':'软件云及安全',
    '互联网内容与信息':'互联网平台','互联网零售':'互联网平台','娱乐':'互联网平台','电子游戏与多媒体':'互联网平台',
    '航空航天与国防':'商业航天',
    '汽车制造':'汽车','汽车零件':'汽车',
    '铜':'材料及矿业','其他贵金属和采矿':'材料及矿业','铀':'材料及矿业','其他工业金属与采矿':'材料及矿业',
    '特殊化工用品':'材料及矿业','黄金':'材料及矿业','金属加工':'材料及矿业','钢':'材料及矿业',
    '可再生能源公用事业':'能源及电力','太阳能':'能源及电力','独立电力生产商':'能源及电力',
    '电气设备及零件':'能源及电力','油气中游':'能源及电力','油气勘探与开发':'能源及电力',
    '受监管电力':'能源及电力','综合油气':'能源及电力',
    '安全与保护':'工业及服务','专用工业机械':'工业及服务','包装与容器':'工业及服务',
    '个性化服务':'工业及服务','建筑产品与设备':'工业及服务','建筑工程':'工业及服务',
    '租赁服务':'工业及服务','专业商务服务':'工业及服务',
    '休闲':'消费','鞋类及配饰':'消费','非酒精饮料':'消费','多元化REITs':'房地产',
}
OVERRIDES={
    **{s:'光通信' for s in 'VIAV POET AXTI GLW'.split()},
    **{s:'数据中心基础设施' for s in 'IREN CIFR CORZ APLD CRWV NBIS VNET VRT'.split()},
    'BWXT':'能源及电力','LTBR':'能源及电力','GEV':'能源及电力','DQ':'能源及电力',
    'QMCO':'存储','AEVA':'汽车','HSAI':'汽车','OUST':'汽车',
}


def prepare(source=SOURCE):
    source=Path(source)
    rows=list(csv.DictReader(source.open(encoding='utf-8-sig')))
    audit=[]
    seen=set()
    sector=dict(SECTOR)
    for r in rows:
        code=r['代码']
        s=code.removesuffix('.US')
        reason='included'
        if not code.endswith('.US') or r['市场']!='美股':
            reason='non_us'
        elif s.startswith('.'):
            reason='index'
        elif any(t in r['名称'].upper() for t in ('ETF','ETN','基金')):
            reason='fund_not_stock'
        elif r['所属行业']=='-':
            reason='otc_or_identity_unverified'
        if s=='GOOG':
            s='GOOGL'
            reason='same_issuer_use_existing_GOOGL'
        if reason in ('included','same_issuer_use_existing_GOOGL'):
            if s not in sector:
                sector[s]=OVERRIDES.get(s,INDUSTRIES[r['所属行业']])
            if s in seen:
                reason='duplicate'
            seen.add(s)
        audit.append(dict(original_code=code,symbol=s,name=r['名称'],industry=r['所属行业'],
            sector=sector.get(s,''),status=reason))
    ROOT.mkdir(parents=True,exist_ok=True)
    payload=dict(source=str(source),rows=len(rows),list_symbols=sorted(seen),
        merged_symbols=sorted(set(SECTOR)|seen),sectors=sector,audit=audit)
    (ROOT/'universe.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(json.dumps(dict(rows=len(rows),list_stocks=len(seen),overlap=len(seen&set(SECTOR)),
        additions=len(seen-set(SECTOR)),merged=len(sector),excluded=[r for r in audit if r['status'] not in ('included','same_issuer_use_existing_GOOGL')]),ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', default=str(SOURCE), help='Watchlist CSV path')
    prepare(parser.parse_args().source)

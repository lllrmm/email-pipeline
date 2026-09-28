#!/usr/bin/env python3
"""Scan a time range and enqueue emails that still require summarization."""
from __future__ import annotations
import argparse,json,os,subprocess,sys
from pathlib import Path
from typing import Any
import yaml
SCRIPT_DIR=Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR,SCRIPT_DIR/"src"):
    if str(candidate) not in sys.path: sys.path.insert(0,str(candidate))
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
DEFAULT_CONFIG=SCRIPT_DIR/"daily-mail-pipeline.yaml"
DEFAULT_OUTPUT_ROOT=Path.home()/".hermes/email/daily"
def run_stage(command:list[str])->dict[str,Any]:
    cp=subprocess.run(command,text=True,capture_output=True,check=False)
    if cp.stderr: print(cp.stderr,file=sys.stderr,end="")
    if cp.returncode!=0: raise RuntimeError((cp.stderr or cp.stdout)[-1000:])
    value=json.loads(cp.stdout)
    if not value.get("ok"): raise RuntimeError(str(value))
    return value
def enqueue_scan_log(path:Path,config_path:Path)->dict[str,Any]:
    log=json.loads(path.read_text(encoding="utf-8")); config=yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}; identity_cfg=config.get("identity") or {}; account=str(identity_cfg.get("account") or "outlook"); database=Path(identity_cfg.get("database_path") or (Path.home()/".hermes/email/mail-index.sqlite3")).expanduser().resolve(); index=MailIdentityIndex(database); queued=skipped=0
    for mail in log.get("mails") or []:
        inserted=index.enqueue_event(account=account,rfc_message_id=str(mail["rfc_message_id"]),folder=str(mail["folder"]),uidvalidity=int(mail["uidvalidity"]),uid=int(mail["uid"]),sent_at=mail.get("sent_at"))
        if inserted: queued+=1
        else: skipped+=1
    return {"date":log.get("date"),"messages_total":len(log.get("mails") or []),"queued":queued,"skipped":skipped,"mailboxes_total":log.get("mailboxes_total"),"mailboxes_failed":log.get("mailboxes_failed") or []}
def main()->int:
    os.umask(0o077); p=argparse.ArgumentParser(); p.add_argument("--date"); p.add_argument("--from",dest="date_from"); p.add_argument("--to",dest="date_to"); p.add_argument("--mailbox",action="append"); p.add_argument("--limit-per-mailbox",type=int,default=200); p.add_argument("--config",type=Path,default=DEFAULT_CONFIG); p.add_argument("--output-root",type=Path,default=DEFAULT_OUTPUT_ROOT); p.add_argument("--scan-log",type=Path); a=p.parse_args(); config=a.config.expanduser().resolve()
    try:
        if a.scan_log: paths=[a.scan_log.expanduser().resolve()]
        else:
            cmd=[sys.executable,str(SCRIPT_DIR/"scan_mails.py"),"--config",str(config),"--output-root",str(a.output_root.expanduser()),"--limit-per-mailbox",str(a.limit_per_mailbox)]
            for flag,val in (("--date",a.date),("--from",a.date_from),("--to",a.date_to)):
                if val: cmd.extend([flag,val])
            for mailbox in a.mailbox or []: cmd.extend(["--mailbox",mailbox])
            result=run_stage(cmd); paths=[Path(item["scan_log_path"]) for item in result.get("per_day") or []] if result.get("mode")=="range" else [Path(result["scan_log_path"])]
        per_day=[enqueue_scan_log(path,config) for path in paths]; print(json.dumps({"ok":True,"mode":"range" if len(per_day)>1 else "single","per_day":per_day},ensure_ascii=False,indent=2)); return 0
    except Exception as exc: print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False)); return 1
if __name__=="__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""PostgreSQL backup/restore tooling. No credentials in process argv or logs. Encrypted storage is required operationally."""
import argparse,hashlib,json,os,subprocess,tempfile
from datetime import datetime,UTC
from pathlib import Path
from urllib.parse import urlsplit,unquote

def connection(url:str)->dict[str,str]:
    parts=urlsplit(url.replace("postgresql+asyncpg://","postgresql://",1))
    if parts.scheme!="postgresql" or not parts.hostname or not parts.username or not parts.path.strip("/"):raise ValueError("Explicit PostgreSQL connection URL required")
    env=os.environ.copy()
    env.update(PGHOST=parts.hostname,PGPORT=str(parts.port or 5432),PGUSER=unquote(parts.username),PGDATABASE=unquote(parts.path.strip("/")))
    if parts.password:env["PGPASSWORD"]=unquote(parts.password)
    env["PGCONNECT_TIMEOUT"]="10"
    return env

def digest(path:Path)->str:
    value=hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda:file.read(1024*1024),b""):value.update(block)
    return value.hexdigest()

def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["backup","verify","restore"])
    parser.add_argument("path",type=Path)
    parser.add_argument("--allow-restore-into-empty-database",action="store_true")
    args=parser.parse_args();path=args.path.resolve();manifest=path.with_suffix(path.suffix+".json")
    if args.action=="backup":
        if path.exists() or manifest.exists():raise SystemExit("Refusing to overwrite a backup")
        env=connection(os.environ["LEASEGUARD_DATABASE_URL"])
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        handle,temp=tempfile.mkstemp(prefix=".backup-",dir=path.parent);os.close(handle)
        try:
            subprocess.run(["pg_dump","--format=custom","--no-owner","--no-privileges","--file",temp],env=env,check=True,capture_output=True)
            os.link(temp,path) # atomic no-clobber publication; original private file permissions retained
            with manifest.open("x") as output:json.dump({"created_at":datetime.now(UTC).isoformat(),"sha256":digest(path),"format":"pg_dump-custom","encrypted_storage_required":True},output,indent=2)
            os.chmod(manifest,0o600)
        finally:Path(temp).unlink(missing_ok=True)
        print("Backup captured. Verify and rehearse restore; database backup is not a backup of S3/Qdrant/Neo4j.")
    else:
        metadata=json.loads(manifest.read_text())
        if digest(path)!=metadata["sha256"]:raise SystemExit("Backup checksum mismatch")
        subprocess.run(["pg_restore","--list",str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if args.action=="restore":
            if not args.allow_restore_into_empty_database:raise SystemExit("Explicit empty-database restore acknowledgement required")
            env=connection(os.environ["LEASEGUARD_RESTORE_DATABASE_URL"])
            check=subprocess.run(["psql","-Atc","SELECT count(*) FROM pg_tables WHERE schemaname='public'"],env=env,check=True,capture_output=True,text=True)
            if check.stdout.strip()!="0":raise SystemExit("Restore target must be empty")
            subprocess.run(["pg_restore","--exit-on-error","--single-transaction","--no-owner","--no-privileges","--dbname",env["PGDATABASE"],str(path)],env=env,check=True,capture_output=True)
            print("Restore completed; validate schema, audit anchors, originals and application invariants before promotion.")
        else:print("Archive checksum and catalog verified; this is not a restore rehearsal.")
if __name__=="__main__":
    try:main()
    except subprocess.CalledProcessError:raise SystemExit("PostgreSQL operation failed; inspect restricted database/client logs. No credentials echoed.") from None

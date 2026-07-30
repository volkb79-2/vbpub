# Fixes to soulmask egg

## install and start

```
Scenario       │ File exists? │ Writable?  │ Run steamcmd?
───────────────┼──────────────┼────────────┼──────────────── 
Fresh install  │ No           │ —          │ Yes (! -f true) 
Normal disk    │ Yes          │ Yes        │ Yes (-w true) 
Ramdisk active │ Yes          │ No (EROFS) │ No — skipped  
```


@echo off
rem Stops the S4PC Catalyst cleanly via its API.
setlocal
where py >nul 2>nul
if %errorlevel%==0 ( set "PYCMD=py -3" ) else ( set "PYCMD=python" )
%PYCMD% -c "import os,base64,urllib.request;req=urllib.request.Request('http://127.0.0.1:8321/api/shutdown',data=b'{}');p=os.environ.get('S4PC_ACCESS_PASSWORD');(req.add_header('Authorization','Basic '+base64.b64encode((os.environ.get('S4PC_ACCESS_USER','team')+':'+p).encode()).decode()) if p else None);urllib.request.urlopen(req,timeout=5);print('Catalyst stopped.')" 2>nul || echo Catalyst was not running.
pause

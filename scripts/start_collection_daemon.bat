 @echo off                                                                                                                                      
  REM ============================================================                                                                               
  REM Data Collection Daemon - Task Scheduler Version                                                                                            
  REM ============================================================                                                                               
                                                                                                                                                 
  REM Log file path                                                                                                                              
  set LOGFILE=C:\Users\Nick\PycharmProjects\option_trading\output\log\task_scheduler_startup.log                                                 
                                                                                                                                                 
  REM Create log directory if it doesn't exist                                                                                                   
  if not exist "C:\Users\Nick\PycharmProjects\option_trading\output\log" mkdir "C:\Users\Nick\PycharmProjects\option_trading\output\log"         
                                                                                                                                                 
  echo ============================================================ > "%LOGFILE%"                                                                
  echo TASK SCHEDULER STARTUP LOG >> "%LOGFILE%"                                                                                                 
  echo ============================================================ >> "%LOGFILE%"                                                               
  echo Start time: %date% %time% >> "%LOGFILE%"                                                                                                  
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  REM Change to project directory                                                                                                                
  echo Changing to project directory... >> "%LOGFILE%"                                                                                           
  cd /d "C:\Users\Nick\PycharmProjects\option_trading"                                                                                           
  if errorlevel 1 (                                                                                                                              
      echo ERROR: Failed to change directory >> "%LOGFILE%"                                                                                      
      exit /b 1                                                                                                                                  
  )                                                                                                                                              
  echo Current directory: %CD% >> "%LOGFILE%"                                                                                                    
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  REM Check if virtual environment exists                                                                                                        
  echo Checking virtual environment... >> "%LOGFILE%"                                                                                            
  if not exist ".venv\Scripts\python.exe" (                                                                                                      
      echo ERROR: Virtual environment not found >> "%LOGFILE%"                                                                                   
      exit /b 1                                                                                                                                  
  )                                                                                                                                              
  echo Virtual environment found >> "%LOGFILE%"                                                                                                  
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  REM Activate virtual environment                                                                                                               
  echo Activating virtual environment... >> "%LOGFILE%"                                                                                          
  call .venv\Scripts\activate.bat                                                                                                                
  if errorlevel 1 (                                                                                                                              
      echo ERROR: Failed to activate virtual environment >> "%LOGFILE%"                                                                          
      exit /b 1                                                                                                                                  
  )                                                                                                                                              
  echo Virtual environment activated >> "%LOGFILE%"                                                                                              
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  REM Wait for network (NO BREAK - non-interactive)                                                                                              
  echo Waiting 10 seconds for network initialization... >> "%LOGFILE%"                                                                           
  timeout /t 10 /nobreak > nul 2>&1                                                                                                              
  echo Network wait complete >> "%LOGFILE%"                                                                                                      
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  REM Start the daemon                                                                                                                           
  echo Starting collection daemon... >> "%LOGFILE%"                                                                                              
  echo Command: python -m coding.service.data_collection.collection_daemon >> "%LOGFILE%"                                                        
  echo. >> "%LOGFILE%"                                                                                                                           
                                                                                                                                                 
  python -m coding.service.data_collection.collection_daemon >> "%LOGFILE%" 2>&1                                                                 
                                                                                                                                                 
  REM Daemon stopped (shouldn't get here unless error or manual stop)                                                                            
  echo. >> "%LOGFILE%"                                                                                                                           
  echo ============================================================ >> "%LOGFILE%"                                                               
  echo DAEMON STOPPED >> "%LOGFILE%"                                                                                                             
  echo ============================================================ >> "%LOGFILE%"                                                               
  echo Stop time: %date% %time% >> "%LOGFILE%"                                                                                                   
  echo Exit code: %errorlevel% >> "%LOGFILE%"
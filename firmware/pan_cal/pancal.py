#!/usr/bin/env python3
# usage: pancal.py CMD [wait_s]   e.g. "U 1600" 3, "?" 0   -- sends one line to pan_cal, prints replies
import os, sys, termios, time, select
fd = os.open("/dev/serial0", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd); a[0]=0; a[1]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL; a[3]=0; a[4]=a[5]=termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a); termios.tcflush(fd, termios.TCIOFLUSH)
def rd(sec):
    end=time.time()+sec; buf=b""
    while time.time()<end:
        if select.select([fd],[],[],0.05)[0]: buf+=os.read(fd,256)
    return buf.decode(errors="replace").strip().replace("\r\n"," | ")
cmd = sys.argv[1]; wait = float(sys.argv[2]) if len(sys.argv) > 2 else 0
os.write(fd, (cmd+"\n").encode()); print(cmd, "->", rd(0.3))
if wait:
    time.sleep(wait); os.write(fd, b"?\n"); print("? ->", rd(0.3))

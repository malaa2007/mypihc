#!/usr/bin/python
#
# pir.py
# Detect movement using a PIR module
#
# Author : Joe Lones
# Date   : 21/01/2013

# Import required Python libraries
import RPi.GPIO as GPIO
import time
import signal
import sys
import subprocess
import os
import logging
import ConfigParser
import datetime
import threading
import glob
import smtplib
import shlex

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def signal_handler(signal, frame):
    global c, t1Stop, t1, already_armed, already_recording
    log.warn('Application received signal to exit!')
    if already_armed and c['arm_camera']:
        log.info("Disarming camera(s)...")
        armCamera(0)

    if already_recording and ['record_on_motion']:
        log.info("Stopping recording...")

        t1Stop.set()
        t1.join()
        if t1.isAlive():
            log.debug("Thread alive")
        else:
            log.debug("Thread dead")
        log.info("Recorded: " + str(countFiles()) + " file(s).")
    log.warn('Program exit!')
    GPIO.cleanup()
    sys.exit(0)


def armCamera(flag):
    global already_armed, c

    if flag == 1:
        if already_armed == 2:
            log.info("Camera %s already armed" % c['cam_ip'])
            return
        else:
            log.info("Arming camera %s" % c['cam_ip'])
            already_armed += 1

    if flag == 0:
        log.info("Disarming camera %s" % c['cam_ip'])
        already_armed = 0

    command = [
        c['path_to_wget'],
        '-q',
        '-O',
        '-',
        'http://' \
            + c['cam_ip'] \
            + '/set_alarm.cgi?motion_armed=1&motion_sensitivity=' \
            + c['cam_motion_sensitivity'] \
            + '&mail=' \
            + str(flag) \
            + '&user=' \
            + c['cam_user'] \
            + '&pwd=' \
            + c['cam_password']
    ]
    log.debug(' '.join(command))
    p = subprocess.Popen(command, stdout=subprocess.PIPE)
    stdout, stderr = p.communicate()
    log.debug('Command Output: ' + stdout.strip(' \t\n\r'))


def sendEmail(toAddr, fromAddr, subject, body):
    global c, log

    if not all ([['email_smtp'], c['email_smtp_port'], c['email_user'], c['email_passwd'], toAddr]):
        log.error("Cannot send email due to missing parameters")
        return

    """With this function we send out our html email"""
    # Create message container - the correct MIME type is multipart/alternative here!
    message = MIMEMultipart('alternative')
    message['subject'] = subject
    message['To'] = toAddr
    message['From'] = fromAddr
    message.preamble = """
                        Your mail reader does not support the report format.
                        Please visit us <a href="http://www.mysite.com">online</a>!"""

    # Record the MIME type text/html.
    htmlBody = MIMEText(body, 'html', _charset="UTF-8")

    # Attach parts into message container.
    # According to RFC 2046, the last part of a multipart message, in this case
    # the HTML message, is best and preferred.
    message.attach(htmlBody)

    # The actual sending of the e-mail
    smtp = c['email_smtp']+':'+str(c['email_smtp_port'])
    server = smtplib.SMTP(smtp)

    # Print debugging output when testing
    # server.set_debuglevel(1)
    server.starttls()
    server.login(c['email_user'], c['email_passwd'])
    server.sendmail(fromAddr, toAddr, message.as_string())
    server.quit()


def startRecord(arg, stop_event):
    global c, log
    p = None

    target_dir = os.path.join(c['save_to_dir'], 'pir')
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    now = time.time()
    for f in os.listdir(target_dir):
        f = os.path.join(target_dir, f)
        if os.stat(f).st_mtime < now - c['cam_days_to_purge'] * 86400:
            if os.path.isfile(f):
                os.remove(os.path.join(target_dir, f))

    while(not stop_event.is_set()):
        log.debug("In thread...")
        if p:
            p.terminate()
        
        cmd = c['path_to_ffmpeg'] + ' -use_wallclock_as_timestamps 1 -f mjpeg -i "http://' + c['cam_ip'] + '/videostream.cgi?user=' \
            + c['cam_user'] + '&pwd=' + c['cam_password'] + '" -i "http://' + c['cam_ip'] + '/videostream.asf?user=' + c['cam_user'] \
            + '&pwd=' + c['cam_password'] + '" -map 0:v -map 1:a -acodec copy -vcodec copy '  \
            + os.path.join(target_dir, c['cam_prefix_file_name'] + '_' + datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '.mkv') 
        log.debug(shlex.split(cmd))

        p = subprocess.Popen(
            shlex.split(cmd),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        stop_event.wait(c['cam_record_length'])
    log.debug("Stopping thread...")
    p.terminate()


def getSettings(s):
    camera_to_use_record = None
    filename = "config.ini"
    config = ConfigParser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), '../..', filename))

    s['log_file'] = 'pir.log'
    s['email_on_motion'] = config.get('pir', 'email_on_motion')
    s['record_on_motion'] = config.get('pir', 'record_on_motion')
    s['arm_camera'] = config.get('pir', 'arm_camera')
    s['send_sms'] = config.get('pir', 'send_sms')

    for i in ['camera1', 'camera2']:
        if config.get('pir', 'record_with_'+i):
            camera_to_use_record = i
  
    if (camera_to_use_record in config.sections()):
        s['cam_ip'] = config.get(camera_to_use_record, 'ip')
        s['cam_user'] = config.get(camera_to_use_record, 'username')
        s['cam_password'] = config.get(camera_to_use_record, 'password')
        s['cam_prefix_file_name'] = config.get(camera_to_use_record, 'name')
        s['cam_motion_sensitivity'] = config.get(camera_to_use_record, 'cam_motion_sensitivity')
    else:
        print ("Error selecting \"%s\" for recording, exiting" % camera_to_use_record)
        sys.exit(1)

    s['path_to_wget'] =  config.get('config', 'path_to_wget')
    if not (os.path.isfile(s['path_to_wget']) and os.access(s['path_to_wget'], os.X_OK)):
        print ("Path to wget: " + s['path_to_wget'] + " not set correctly")
        sys.exit(1)

    s['path_to_ffmpeg'] =  config.get('config', 'path_to_ffmpeg')
    if not (os.path.isfile(s['path_to_ffmpeg']) and os.access(s['path_to_ffmpeg'], os.X_OK)):
        print ("Path to ffmpeg: " + s['path_to_ffmpeg'] + " not set correctly")
        sys.exit(1)

    s['cam_record_length'] = int(config.get('config', 'cam_record_length'))
    s['cam_days_to_purge'] = int(config.get('config', 'days_to_purge'))
    s['save_to_dir'] = config.get('config', 'save_to_dir')

    s['email_from_addr'] = config.get('email', 'email_from_addr')
    s['email_user'] = config.get('email', 'email_user')
    s['email_passwd'] = config.get('email', 'email_passwd')
    s['email_smtp'] = config.get('email', 'email_smtp')

    s['email_smtp_port'] = config.get('email', 'email_smtp_port')
    s['email_send_to'] = config.get('email', 'email_send_to')
    s['email_sms'] = config.get('email', 'email_sms')

    # check if save to directory exists
    if not os.path.isdir(s['save_to_dir']):
        print ("Directory: " + s['save_to_dir'] + " does not exist, exiting")
        sys.exit(1)


def countFiles():
    global c
    return len([f for f in glob.glob(os.path.join(c['save_to_dir'], c['cam_prefix_file_name']+'*.asf')) if os.path.isfile(f)])

if __name__ == '__main__':
    # global variables
    already_armed = 0
    already_sent = 0
    already_recording = 0
    already_sent_sms = 0

    c = {}
    t1Stop = None
    t1 = None

    # config data
    getSettings(c)
    
    # configure logging
    log = logging.getLogger('pir')
    log.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    handler_stream = logging.StreamHandler()
    handler_stream.setFormatter(formatter)
    #handler_stream.setLevel(logging.ERROR)
    log.addHandler(handler_stream)

    handler_file = logging.FileHandler(os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), c['log_file'])))
    handler_file.setFormatter(formatter)
    log.addHandler(handler_file)

    # catch signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Use BCM GPIO references
    # instead of physical pin numbers
    GPIO.setmode(GPIO.BCM)

    #Alerts OFF
    GPIO.setwarnings(False)

    # Define GPIO to use on Pi
    GPIO_PIR = 7

    log.info("PIR Module Test (CTRL-C to exit)")

    # Set pin as input
    GPIO.setup(GPIO_PIR, GPIO.IN)

    Current_State = 0
    Previous_State = 0

    try:
        log.warn("Waiting for PIR to settle ...")

        # Loop until PIR output is 0
        while GPIO.input(GPIO_PIR) == 1:
            Current_State = 0

        log.info("  Ready")
        # Loop until users quits with CTRL-C
        while True:
            # Read PIR state
            Current_State = GPIO.input(GPIO_PIR)

            if Current_State == 1 and Previous_State == 0:
                # PIR is triggered
                log.info("  Motion detected!")
                # sms
                if not already_sent_sms and c['send_sms']:
                    log.info("  Sending sms through email...")
                    sendEmail(c['email_sms'], c['email_from_addr'], 'Motion detected', 'There\'s been movement detected in the house.')
                    already_sent_sms = 1
                # email
                if not already_sent and c['email_on_motion']:
                    log.info("  Sending email...")
                    sendEmail(c['email_send_to'], c['email_from_addr'], 'Motion detected', 'There\'s been movement detected in the house.')
                    already_sent = 1
                # record
                if not already_recording and c['record_on_motion']:
                    log.info("  Starting recording!")
                    t1Stop = threading.Event()
                    t1 = threading.Thread(target=startRecord, args=(
                        1,
                        t1Stop,
                    ))
                    t1.start()
                    already_recording = 1

                # arm camera
                if c['arm_camera']:
                    armCamera(1)
                # Record previous state
                Previous_State = 1
            elif Current_State == 0 and Previous_State == 1:
                # PIR has returned to ready state
                log.info("  Ready")
                Previous_State = 0

            # Wait for 10 milliseconds
            time.sleep(0.01)

    except KeyboardInterrupt:
        log.warn("  Quit")
        # Reset GPIO settings
        GPIO.cleanup()

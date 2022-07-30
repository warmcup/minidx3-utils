from time import sleep
import threading
import pywinusb.hid as hid
import os

lastread = []
live = True
device = None
done = False
option = 0
first_att = False

params = {
    'power_mode' : 2, #real control = 0x0 ; always on = 0xff ; #auto power off = 0x1-0xfe
    'charge_mode' : 3, #low battery = 0x0 ; real charge = 0xff ; #manual charge = 0x1-0xfe
    'buzzer' : 4, #off = 0x0 ; on = 0xff
    'power_save' : 6, #off = 0x0 ; on = 0xff
    'track1' : 7, #disabled = 0x0 ; enabled = 0xff ; #request = 0x1-0xfe
    'track2' : 8, #disabled = 0x0 ; enabled = 0xff ; #request = 0x1-0xfe
    'track3' : 9, #disabled = 0x0 ; enabled = 0xff ; #request = 0x1-0xfe
}

params_by_num = {
    1 : 2,
    2 : 3,
    3 : 4,
    4 : 6,
    5: 7,
    6: 8,
    7: 9,
}

def handler(data):
    lastread.extend(data.copy())

def scan():
    devices = hid.HidDeviceFilter(vendor_id = 2049, product_id = 131).get_devices()
    if not devices:
        return None
    else:
        device = devices[0]
        return device    
    return None

def setup(handler):
    dev = scan()
    if dev:
        dev.open()
        dev.set_raw_data_handler(handler)
        return dev
    return None

'''
next 4 functions taken from https://github.com/mrmoss/minidx3/blob/master/minidx3.py
'''

def crc(data):
	crc = 0
	for i in range(len(data)):
		crc ^= data[i]
	return crc

def array_to_str(arr):
	text = ""
	for i in range(len(arr)):
		text += chr(arr[i])
	return text

def str_to_array(text):
	return [ord(i) for i in text]

def pack(payload):
    if type(payload) is str:
        payload=str_to_array(payload)
    size = [len(payload) & 0xff00, len(payload) & 0x00ff]
    ret = bytes([0x02] + size + payload + [crc(size+payload)] + [0xd])
    return ret

#packet constructor + sender from a given payload
def send_packet(dev, payload):
    dataOut = dev.find_feature_reports()[0]
    final = [0x0] * 65
    i = 1
    for b in pack(payload):
        final[i] = b
        i += 1
    dataOut.set_raw_data(final)
    dataOut.send()

#reply handler
def send_wait_response(dev, payload, wait_time = 0.01):
    send_packet(dev, payload)
    sleep(wait_time * 2)
    while not lastread:
        sleep(wait_time)
    ret = lastread.copy()
    lastread.clear()
    return ret[4:4+ret[3]+1]

#helpers
def get_record_number(dev):
    return int.from_bytes(send_wait_response(dev, 'N')[2:4], "big")

def get_record_by_index(dev, n):
    n_bytes = ''
    #change n into its payload bytecode (2len)
    for b in list(int.to_bytes(n, 2, "big")):
        n_bytes += chr(b)
    data = send_wait_response(dev, 'G' + n_bytes)
    if data[0:2] != [ord('G'), ord('0')]:
        #read error
        return ()
    data = data[3:]
    #date
    year = ''.join(chr(x) for x in data[:4])
    month = ''.join(chr(x) for x in data[4:6])
    dom = ''.join(chr(x) for x in data[6:8])
    hr = ''.join(chr(x) for x in data[8:10])
    mt = ''.join(chr(x) for x in data[10:12])
    sec = ''.join(chr(x) for x in data[12:14])
    date =  year + '/' + month + '/' + dom + ' ' + hr + ':' + mt + ':' + sec

    #track sizes
    sizes = data[15:18]
    data = data[18:]

    temp = []
    for x in data:
        if x > 31 and x < 123:
            temp.append(chr(x))

    data = temp

    #tracks
    track1 = ''.join(data[:sizes[0]])
    track2 = ''.join(data[sizes[0]:sizes[0]+sizes[1]])
    track3 = ''.join(data[sizes[0]+sizes[1]:sizes[0]+sizes[1]+sizes[2]])

    infoStruct = [date, track1, track2, track3]

    for i in range (1, 4):
        if not infoStruct[i]:
            infoStruct[i] = 'No data'
        else:
            infoStruct[i] += '?'
    
    #Fix track 1
    if infoStruct[1] != 'No data' and not infoStruct[1][0] == '%':
        infoStruct[1] = '%' + infoStruct[1]

    #Fix track 2
    if infoStruct[2] != 'No data' and not infoStruct[2][0] == ';':
        infoStruct[2] = ';' + infoStruct[2]

    #Fix track 3
    if infoStruct[3] != 'No data' and not infoStruct[3][0] == '+':
        infoStruct[3] = '+' + infoStruct[3]


    return tuple(infoStruct)

#not needed for obtaining data
def login(dev, pin):
    return not bool(int(chr(send_wait_response(dev, 'L' + pin, 0.0001)[1])))

def logout(dev):
    return send_wait_response(dev, 'O')

def wipe(dev):
    return send_wait_response(dev, 'E')

def crack_pin(dev):
    for i in range(0, 10000):
        print('Testing PIN', str(i).zfill(4))
        if login(dev, str(i).zfill(4)):
            print('PIN found:', str(i).zfill(4))
            break

def connection(dev):
    global live
    while not done:
        if not dev.is_plugged():
            live = False
            print('\nconnection: died')
            exit(2)
        sleep(0.001)

#records
def get_params(dev):
    parameters = send_wait_response(dev, 'B' + chr(0x0) + chr(0x10))[2:]
    return parameters

def set_register(dev, type, param):
    payload = get_params(dev)
    payload[type] = param
    payload_bytes = ''
    for b in payload:
        payload_bytes += chr(b)
    return send_wait_response(dev, 'C' + chr(0x0) + chr(0x10) + payload_bytes)

def get_auto_poweroff_time(dev):
    parameters = get_params(dev)   
    t = parameters[0]*128 + parameters[1]/2
    return int(t)

def set_auto_poweroff_time(dev, t):
    payload = get_params(dev)
    h = t//128
    l = (t-(h*128))*2
    payload[0] = h
    payload[1] = l
    payload_bytes = ''
    for b in payload:
        payload_bytes += chr(b)
    return send_wait_response(dev, 'C' + chr(0x0) + chr(0x10) + payload_bytes)

#got too into making the thing decent to use ^^
def choice(n, args):
    if len(args) != n or n < 2:
        print(len(args))
        print('Wrong number of arguments for choice')
        return -1
    else:
        i = 1
        for arg in args:
            print('{}. '.format(i) + arg)
            i += 1
        r = input("Select an option: ")
        while not (r.strip().isdigit() and int(r.strip()) in range(1, n + 1)):
            r = input("Try again.\n::: ")
        return int(r)

def hex_choice(n):
    d = {1 : 0x0, 2 : 0xff, 3 : 0x1}
    return d[n]

def display_settings():
    data = get_params(device)
    info = []
    if data[params['power_mode']] == 0x0:
        info.append('Real control')
    elif data[params['power_mode']] == 0xff:
        info.append('Always on')
    else:
        info.append('Auto power off')
    
    if data[params['charge_mode']] == 0x0:
        info.append('Low battery')
    elif data[params['charge_mode']] == 0xff:
        info.append('Real charge')
    else:
        info.append('Manual charge')

    if data[params['buzzer']] == 0x0:
        info.append('Off')
    elif data[params['buzzer']] == 0xff:
        info.append('On')
    else:
        info.append('Unknown')

    if data[params['power_save']] == 0x0:
        info.append('Off')
    elif data[params['power_save']] == 0xff:
        info.append('On')
    else:
        info.append('Unknown')

    for t in data[7:10]:
        if t == 0x0:
            info.append('Disabled')
        elif t == 0xff:
            info.append('Enabled')
        else:
            info.append('Request')
    if info[0] == 'Auto power off':
        print('Power mode: ', info[0], '- Auto power off time:', str(get_auto_poweroff_time(device)) + 's')
    else:
        print('Power mode: ', info[0])
    print('Charge mode: ', info[1])
    print('Buzzer: ', info[2])
    print('Power save: ', info[3])
    print('Track status: ', info[4:7])



#start of execution

if os.name != 'nt':
    exit(3)

print('Attempting to attach to a device...')
device = setup(handler)
if device:
    print('Success!')
else:    
    print('No devices found')
    exit(1)

#thread for checking connection status
connection_t = threading.Thread(target=connection, args=(device,))
connection_t.start()

clear_screen = lambda: os.system('cls')


while option == 0:
    
    clear_screen()
    print("Menu:")
    option = choice(5, ['Dump all records', 'Delete all records', 'Crack PIN', 'Device settings', 'Exit'])
    if option == 1:
        n = get_record_number(device)
        if n:
            print('\nDumping all records:')
            for i in range(n):
                record = get_record_by_index(device, i)
                if record:
                    print(record)
            print('\nDone')
        else:
            print('No records in the device, press any key to return to menu')
        input()
    elif option == 2:
        wipe(device)
        print('Done')
        input()
    elif option == 3:
        crack_pin(device)
        print('Done')
        input()
    elif option == 4:
        option_2 = 0
        while option_2 == 0:
            clear_screen()
            print("Device settings:")
            display_settings()
            print("Settings menu:")
            settings = [x.capitalize() for x in params]
            settings += ['Set auto power off time' , 'Back to main menu']
            
            option_2 = choice(len(settings), settings)
            while option_2 not in range(1, len(params) + 3):
                option_2 = int(input("Try again: "))
            if option_2 == 1:
                n = hex_choice(choice(3, ['Real control', 'Always on', 'Auto power off']))
                set_register(device, params_by_num[option_2], n)
            elif option_2 == 2:
                n = hex_choice(choice(3, ['Low battery', 'Real charge', 'Manual charge']))
                set_register(device, params_by_num[option_2], n)
            elif option_2 == 3 or option_2 == 4:
                n = hex_choice(choice(2, ['Off', 'On']))
                set_register(device, params_by_num[option_2], n)
            elif option_2 == 5 or option_2 == 6 or option_2 == 7:
                n = hex_choice(choice(3, ['Disabled', 'Enabled', 'Request']))
                set_register(device, params_by_num[option_2], n)
            elif option_2 == 8:
                r = input("::: ")
                while not (r.strip().isdigit() and int(r.strip()) > -1 and int(r.strip()) < 32896):
                    r = input("Try again.\n::: ")
                set_auto_poweroff_time(device, int(r))

            if option_2 == 9:
                option_2 == -1
            else:
                option_2 = 0
            

    else:
        device.close()
        done = True
        exit(0)
    option = 0


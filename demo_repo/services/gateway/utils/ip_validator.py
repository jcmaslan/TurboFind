import ipaddress

def is_private_ip(ip_str):
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.is_private
    except ValueError:
        return False

def is_blocked(ip_str, blocklist):
    return ip_str in blocklist

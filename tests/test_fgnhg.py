import os
import re
import time
import json
import pytest

from dvslib.dvs_common import wait_for_result
from swsscommon import swsscommon

IF_TB = 'INTERFACE'
VLAN_TB = 'VLAN'
VLAN_MEMB_TB = 'VLAN_MEMBER'
VLAN_IF_TB = 'VLAN_INTERFACE'
VLAN_IF = 'VLAN_INTERFACE'
FG_NHG = 'FG_NHG'
FG_NHG_PREFIX = 'FG_NHG_PREFIX'
FG_NHG_MEMBER = 'FG_NHG_MEMBER'
ROUTE_TB = "ROUTE_TABLE"
ASIC_ROUTE_TB = "ASIC_STATE:SAI_OBJECT_TYPE_ROUTE_ENTRY"
ASIC_NHG_MEMB = "ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP_MEMBER"
ASIC_NH_TB = "ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP"


def create_entry(db, table, key, pairs):
    db.create_entry(table, key, pairs)
    programmed_table = db.wait_for_entry(table,key)
    assert programmed_table != {}


def remove_entry(db, table, key):
    db.delete_entry(table, key)
    db.wait_for_deleted_entry(table,key)


def get_asic_route_key(asic_db, ipprefix):
    route_exists = False
    key = ''
    keys = asic_db.get_keys(ASIC_ROUTE_TB)
    for k in keys:
        rt_key = json.loads(k)

        if rt_key['dest'] == ipprefix:
            route_exists = True
            key = k
            break
    assert route_exists
    return key


def validate_asic_nhg_fine_grained_ecmp(asic_db, ipprefix, size):
    def _access_function():
        false_ret = (False, '')
        keys = asic_db.get_keys(ASIC_ROUTE_TB)
        key = ''
        route_exists = False
        for k in keys:
            rt_key = json.loads(k)
            if rt_key['dest'] == ipprefix:
                route_exists = True
                key = k
        if not route_exists:
            return false_ret

        fvs = asic_db.get_entry(ASIC_ROUTE_TB, key)
        if not fvs:
            return false_ret

        nhgid = fvs.get("SAI_ROUTE_ENTRY_ATTR_NEXT_HOP_ID")
        fvs = asic_db.get_entry("ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP", nhgid)
        if not fvs:
            return false_ret

        nhg_type = fvs.get("SAI_NEXT_HOP_GROUP_ATTR_TYPE")
        if nhg_type != "SAI_NEXT_HOP_GROUP_TYPE_FINE_GRAIN_ECMP":
            return false_ret
        nhg_cfg_size = fvs.get("SAI_NEXT_HOP_GROUP_ATTR_CONFIGURED_SIZE")
        if int(nhg_cfg_size) != size:
            return false_ret
        return (True, nhgid)

    _, result = wait_for_result(_access_function,
        failure_message="Fine Grained ECMP route not found")
    return result


def validate_asic_nhg_regular_ecmp(asic_db, ipprefix):
    def _access_function():
        false_ret = (False, '')
        keys = asic_db.get_keys(ASIC_ROUTE_TB)
        key = ''
        route_exists = False
        for k in keys:
            rt_key = json.loads(k)
            if rt_key['dest'] == ipprefix:
                route_exists = True
                key = k
        if not route_exists:
            return false_ret

        fvs = asic_db.get_entry(ASIC_ROUTE_TB, key)
        if not fvs:
            return false_ret

        nhgid = fvs.get("SAI_ROUTE_ENTRY_ATTR_NEXT_HOP_ID")
        fvs = asic_db.get_entry("ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP_GROUP", nhgid)
        if not fvs:
            return false_ret

        nhg_type = fvs.get("SAI_NEXT_HOP_GROUP_ATTR_TYPE")
        if nhg_type != "SAI_NEXT_HOP_GROUP_TYPE_DYNAMIC_UNORDERED_ECMP":
            return false_ret
        return (True, nhgid)
    _, result = wait_for_result(_access_function, failure_message="SAI_NEXT_HOP_GROUP_TYPE_DYNAMIC_UNORDERED_ECMP not found")
    return result


def get_nh_oid_map(asic_db):
    nh_oid_map = {}
    keys = asic_db.get_keys(ASIC_NH_TB)
    for key in keys:
        fvs = asic_db.get_entry("ASIC_STATE:SAI_OBJECT_TYPE_NEXT_HOP", key)
        assert fvs != {}
        nh_oid_map[key] = fvs["SAI_NEXT_HOP_ATTR_IP"]

    assert nh_oid_map != {}
    return nh_oid_map


def verify_programmed_fg_asic_db_entry(asic_db,nh_memb_exp_count,nh_oid_map,nhgid,bucket_size):
    def _access_function():
        false_ret = (False, None)
        ret = True
        nh_memb_count = {}
        for key in nh_memb_exp_count:
            nh_memb_count[key] = 0

        members = asic_db.get_keys(ASIC_NHG_MEMB)
        memb_dict = {}

        for member in members:
            fvs = asic_db.get_entry(ASIC_NHG_MEMB, member)
            if fvs == {}:
                return false_ret
            index = -1
            nh_oid = "0"
            for key, val in fvs.items():
                if key == "SAI_NEXT_HOP_GROUP_MEMBER_ATTR_INDEX":
                    index = int(val)
                elif key == "SAI_NEXT_HOP_GROUP_MEMBER_ATTR_NEXT_HOP_ID":
                    nh_oid = val
                elif key == "SAI_NEXT_HOP_GROUP_MEMBER_ATTR_NEXT_HOP_GROUP_ID":
                    if nhgid != val:
                        print("Expected nhgid of " + nhgid + " but found " + val)
                        return false_ret
            if (index == -1 or
               nh_oid == "0" or
               nh_oid_map.get(nh_oid,"NULL") == "NULL" or
               nh_oid_map.get(nh_oid) not in nh_memb_exp_count):
                print("Invalid nh: nh_oid " + nh_oid + " index " + str(index))
                if nh_oid_map.get(nh_oid,"NULL") == "NULL":
                    print("nh_oid is null")
                if nh_oid_map.get(nh_oid) not in nh_memb_exp_count:
                    print("nh_memb_exp_count is " + str(nh_memb_exp_count) + " nh_oid_map val is " + nh_oid_map.get(nh_oid))
                return false_ret
            memb_dict[index] = nh_oid_map.get(nh_oid)
        idxs = [0]*bucket_size
        for idx,memb in memb_dict.items():
            nh_memb_count[memb] = 1 + nh_memb_count[memb]
            idxs[idx] = idxs[idx] + 1

        for key in nh_memb_exp_count:
            ret = ret and (nh_memb_count[key] == nh_memb_exp_count[key])
        for idx in idxs:
            ret = ret and (idx == 1)
        if ret != True:
            print("Expected member count was " + str(nh_memb_exp_count) + " Received was " + str(nh_memb_count))
            print("Indexes arr was " + str(idxs))
        return (ret, nh_memb_count)

    status, result = wait_for_result(_access_function)
    assert status, f"Exact match not found: expected={nh_memb_exp_count}, received={result}"

    return result


def shutdown_link(dvs, db, port):
    dvs.servers[port].runcmd("ip link set down dev eth0") == 0
    db.wait_for_field_match("PORT_TABLE", "Ethernet%d" % (port * 4), {"oper_status": "down"})


def startup_link(dvs, db, port):
    dvs.servers[port].runcmd("ip link set up dev eth0") == 0
    db.wait_for_field_match("PORT_TABLE", "Ethernet%d" % (port * 4), {"oper_status": "up"})

def run_warm_reboot(dvs):
    dvs.runcmd("config warm_restart enable swss")

    # Stop swss before modifing the configDB
    dvs.stop_swss()

    # start to apply new port_config.ini
    dvs.start_swss()
    dvs.runcmd(['sh', '-c', 'supervisorctl start neighsyncd'])
    dvs.runcmd(['sh', '-c', 'supervisorctl start restore_neighbors'])

    # Enabling some extra logging for validating the order of orchagent
    dvs.runcmd("swssloglevel -l INFO -c orchagent")

def verify_programmed_fg_state_db_entry(state_db,nh_memb_exp_count):
    memb_dict = nh_memb_exp_count
    keys = state_db.get_keys("FG_ROUTE_TABLE")
    assert  len(keys) !=  0
    for key in keys:
        fvs = state_db.get_entry("FG_ROUTE_TABLE", key)
        assert fvs != {}
        for key, value in fvs.items():
            assert value in nh_memb_exp_count
            memb_dict[value] = memb_dict[value] - 1

    for idx,memb in memb_dict.items():
        assert memb == 0


def validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size):
    state_db_entry_memb_exp_count = {}

    for ip, cnt in nh_memb_exp_count.items():
        state_db_entry_memb_exp_count[ip + '@' + ip_to_if_map[ip]] = cnt

    verify_programmed_fg_asic_db_entry(asic_db,nh_memb_exp_count,nh_oid_map,nhgid,bucket_size)
    verify_programmed_fg_state_db_entry(state_db, state_db_entry_memb_exp_count)


def program_route_and_validate_fine_grained_ecmp(app_db, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size):
    ips = ""
    ifs = ""
    for ip in nh_memb_exp_count:
        if ips == "":
            ips = ip
            ifs = ip_to_if_map[ip]
        else:
            ips = ips + "," + ip
            ifs = ifs + "," + ip_to_if_map[ip]

    ps = swsscommon.ProducerStateTable(app_db, ROUTE_TB)
    fvs = swsscommon.FieldValuePairs([("nexthop", ips), ("ifname", ifs)])
    ps.set(fg_nhg_prefix, fvs)
    validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

def fgnhg_clean_up(config_db, asic_db, app_db, state_db, fg_nhg_name, fg_nhg_prefix, active_nhs):
    # remove fgnhg prefix: The fine grained route should transition to regular ECMP/route
    remove_entry(config_db, "FG_NHG_PREFIX", fg_nhg_prefix)

    # Validate regular ECMP
    validate_asic_nhg_regular_ecmp(asic_db, fg_nhg_prefix)
    asic_db.wait_for_n_keys(ASIC_NHG_MEMB, active_nhs)
    state_db.wait_for_n_keys("FG_ROUTE_TABLE", 0)

    # clean up route entry
    asic_rt_key = get_asic_route_key(asic_db, fg_nhg_prefix)
    ps = swsscommon.ProducerStateTable(app_db.db_connection, ROUTE_TB)
    ps._del(fg_nhg_prefix)
    asic_db.wait_for_deleted_entry(ASIC_ROUTE_TB, asic_rt_key)
    asic_db.wait_for_n_keys(ASIC_NHG_MEMB, 0)

    # Cleanup all FG, arp and interface
    remove_entry(config_db, "FG_NHG", fg_nhg_name)

class TestFineGrainedNextHopGroup(object):
    def test_route_fgnhg(self, dvs, testlog):
        app_db = dvs.get_app_db()
        asic_db = dvs.get_asic_db()
        config_db = dvs.get_config_db()
        state_db = dvs.get_state_db()
        fvs_nul = {"NULL": "NULL"}
        NUM_NHs = 6
        fg_nhg_name = "fgnhg_v4"
        fg_nhg_prefix = "2.2.2.0/24"
        bucket_size = 60
        ip_to_if_map = {}

        fvs = {"bucket_size": str(bucket_size)}
        create_entry(config_db, FG_NHG, fg_nhg_name, fvs)

        fvs = {"FG_NHG": fg_nhg_name}
        create_entry(config_db, FG_NHG_PREFIX, fg_nhg_prefix, fvs)

        for i in range(0,NUM_NHs):
            if_name_key = "Ethernet" + str(i*4)
            vlan_name_key = "Vlan" + str((i+1)*4)
            ip_pref_key = vlan_name_key + "|10.0.0." + str(i*2) + "/31"
            fvs = {"vlanid": str((i+1)*4)}
            create_entry(config_db, VLAN_TB, vlan_name_key, fvs)
            fvs = {"tagging_mode": "untagged"}
            create_entry(config_db, VLAN_MEMB_TB, vlan_name_key + "|" + if_name_key, fvs)
            create_entry(config_db, VLAN_IF_TB, vlan_name_key, fvs_nul)
            create_entry(config_db, VLAN_IF_TB, ip_pref_key, fvs_nul)
            dvs.runcmd("config interface startup " + if_name_key)
            dvs.servers[i].runcmd("ip link set down dev eth0") == 0
            dvs.servers[i].runcmd("ip link set up dev eth0") == 0
            bank = 0
            if i >= NUM_NHs/2:
                bank = 1
            fvs = {"FG_NHG": fg_nhg_name, "bank": str(bank), "link": if_name_key}
            create_entry(config_db, FG_NHG_MEMBER, "10.0.0." + str(1 + i*2), fvs)
            ip_to_if_map["10.0.0." + str(1 + i*2)] = vlan_name_key

        # Wait for the software to receive the entries
        time.sleep(1)

        ps = swsscommon.ProducerStateTable(app_db.db_connection, ROUTE_TB)
        fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.7,10.0.0.9,10.0.0.11"),
            ("ifname", "Vlan16,Vlan20,Vlan24")])
        ps.set(fg_nhg_prefix, fvs)
        # No ASIC_DB entry we can wait for since ARP is not resolved yet,
        # We just use sleep so that the sw receives this entry
        time.sleep(1)

        adb = swsscommon.DBConnector(1, dvs.redis_sock, 0)
        rtbl = swsscommon.Table(adb, ASIC_ROUTE_TB)
        keys = rtbl.getKeys()
        found_route = False
        for k in keys:
            rt_key = json.loads(k)

            if rt_key['dest'] == fg_nhg_prefix:
                found_route = True
                break

        # Since we didn't populate ARP yet, the route shouldn't be programmed
        assert (found_route == False)

        asic_nh_count = len(asic_db.get_keys(ASIC_NH_TB))
        dvs.runcmd("arp -s 10.0.0.1 00:00:00:00:00:01")
        dvs.runcmd("arp -s 10.0.0.3 00:00:00:00:00:02")
        dvs.runcmd("arp -s 10.0.0.5 00:00:00:00:00:03")
        dvs.runcmd("arp -s 10.0.0.9 00:00:00:00:00:05")
        dvs.runcmd("arp -s 10.0.0.11 00:00:00:00:00:06")
        asic_db.wait_for_n_keys(ASIC_NH_TB, asic_nh_count + 5)

        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = get_nh_oid_map(asic_db)

        ### Test scenarios with bank 0 having 0 members up and only bank 1 having members
        # ARP is not resolved for 10.0.0.7, so fg nhg should be created without 10.0.0.7
        nh_memb_exp_count = {"10.0.0.9":30,"10.0.0.11":30}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Resolve ARP for 10.0.0.7
        asic_nh_count = len(asic_db.get_keys(ASIC_NH_TB))
        dvs.runcmd("arp -s 10.0.0.7 00:00:00:00:00:04")
        asic_db.wait_for_n_keys(ASIC_NH_TB, asic_nh_count + 1)
        nh_oid_map = get_nh_oid_map(asic_db)

        # Now that ARP was resolved, 10.0.0.7 should be added as a valid fg nhg member
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.7":30,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 2 next hop and bring up 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.9":60}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring up 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring up some next-hops in bank 0 for the 1st time
        nh_memb_exp_count = {"10.0.0.1":10,"10.0.0.3":10,"10.0.0.5":10,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 1 next-hop from bank 0, and 2 next-hops from bank 1
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.5":15,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 1 member and bring up 1 member in bank 0 at the same time
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.3":15,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 2 members and bring up 1 member in bank 0 at the same time
        nh_memb_exp_count = {"10.0.0.5":30,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring up 2 members and bring down 1 member in bank 0 at the same time
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.3":15,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bringup arbitrary # of next-hops from both banks at the same time
        nh_memb_exp_count = {"10.0.0.1":10,"10.0.0.3":10,"10.0.0.5":10,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring all next-hops in bank 1 down
        nh_memb_exp_count = {"10.0.0.1":20,"10.0.0.3":20,"10.0.0.5":20}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Make next-hop changes to bank 0 members, given bank 1 is still down
        nh_memb_exp_count = {"10.0.0.1":30,"10.0.0.5":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bringup 1 member in bank 1 again
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.5":15,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Test 2nd,3rd memb up in bank
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.5":15,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # bring all links down one by one
        shutdown_link(dvs, app_db, 0)
        shutdown_link(dvs, app_db, 1)
        nh_memb_exp_count = {"10.0.0.5":30,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        shutdown_link(dvs, app_db, 2)
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        shutdown_link(dvs, app_db, 3)
        nh_memb_exp_count = {"10.0.0.9":30,"10.0.0.11":30}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        shutdown_link(dvs, app_db, 4)
        nh_memb_exp_count = {"10.0.0.11":60}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down last link, there shouldn't be a crash or other bad orchagent state because of this
        shutdown_link(dvs, app_db, 5)
        # Nothing to check for in this case, sleep 1s for the shutdown to reach sw
        time.sleep(1)

        # bring all links up one by one
        startup_link(dvs, app_db, 3)
        startup_link(dvs, app_db, 4)
        nh_memb_exp_count = {"10.0.0.7":30,"10.0.0.9":30}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        startup_link(dvs, app_db, 5)
        # Perform a route table update, Update the route to contain 10.0.0.3 as well, since Ethernet4 associated with it
        # is link down, it should make no difference
        fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.1,10.0.0.3,10.0.0.5,10.0.0.7,10.0.0.9,10.0.0.11"),
            ("ifname","Vlan4,Vlan8,Vlan12,Vlan16,Vlan20,Vlan24")])
        ps.set(fg_nhg_prefix, fvs)

        # 10.0.0.11 associated with newly brought up link 5 should be updated in FG ecmp
        # 10.0.0.3 addition per above route table change should have no effect
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        startup_link(dvs, app_db, 2)
        nh_memb_exp_count = {"10.0.0.5":30,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        startup_link(dvs, app_db, 0)
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.5":15,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # remove fgnhg member
        remove_entry(config_db, "FG_NHG_MEMBER", "10.0.0.1")
        nh_memb_exp_count = {"10.0.0.5":30,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # add fgnhg member
        fvs = {"FG_NHG": fg_nhg_name, "bank": "0"}
        create_entry(config_db, FG_NHG_MEMBER, "10.0.0.1", fvs)
        nh_memb_exp_count = {"10.0.0.1":15,"10.0.0.5":15,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Remove route
        asic_rt_key = get_asic_route_key(asic_db, fg_nhg_prefix)
        ps._del(fg_nhg_prefix)

        # validate routes and nhg member in asic db, route entry in state db are removed
        asic_db.wait_for_deleted_entry(ASIC_ROUTE_TB, asic_rt_key)
        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, 0)
        state_db.wait_for_n_keys("FG_ROUTE_TABLE", 0)

        remove_entry(config_db, "FG_NHG_PREFIX", fg_nhg_prefix)
        # Nothing we can wait for in terms of db entries, we sleep here
        # to give the sw enough time to delete the entry
        time.sleep(1)

        # Add an ECMP route, since we deleted the FG_NHG_PREFIX it should see
        # standard(non-Fine grained) ECMP behavior
        fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.7,10.0.0.9,10.0.0.11"),
            ("ifname", "Vlan16,Vlan20,Vlan24")])
        ps.set(fg_nhg_prefix, fvs)
        validate_asic_nhg_regular_ecmp(asic_db, fg_nhg_prefix)
        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, 3)

        # add fgnhg prefix: The regular route should transition to fine grained ECMP
        fvs = {"FG_NHG": fg_nhg_name}
        create_entry(config_db, FG_NHG_PREFIX, fg_nhg_prefix, fvs)

        # Validate the transistion to Fine Grained ECMP
        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = {}
        nh_oid_map = get_nh_oid_map(asic_db)

        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        fgnhg_clean_up(config_db, asic_db, app_db, state_db, fg_nhg_name, fg_nhg_prefix, 3)
        
        # clean up nh entries
        for i in range(0,NUM_NHs):
            if_name_key = "Ethernet" + str(i*4)
            vlan_name_key = "Vlan" + str((i+1)*4)
            ip_pref_key = vlan_name_key + "|10.0.0." + str(i*2) + "/31"
            remove_entry(config_db, VLAN_IF_TB, ip_pref_key)
            remove_entry(config_db, VLAN_IF_TB, vlan_name_key)
            remove_entry(config_db, VLAN_MEMB_TB, vlan_name_key + "|" + if_name_key)
            remove_entry(config_db, VLAN_TB, vlan_name_key)
            dvs.runcmd("config interface shutdown " + if_name_key)
            dvs.servers[i].runcmd("ip link set down dev eth0") == 0
            remove_entry(config_db, "FG_NHG_MEMBER", "10.0.0." + str(1 + i*2))

        ### Create new set of entries with a greater number of FG members and
        ### bigger bucket size such that the # of nhs are not divisible by
        ### bucket size. Different physical interface type for dynamicitiy.
        fg_nhg_name = "new_fgnhg_v4"
        fg_nhg_prefix = "3.3.3.0/24"
        # Test with non-divisible bucket size
        bucket_size = 128
        NUM_NHs = 10

        ip_to_if_map = {}
        nh_oid_map = {}

        # Initialize base config
        fvs = {"bucket_size": str(bucket_size)}
        create_entry(config_db, FG_NHG, fg_nhg_name, fvs)

        fvs = {"FG_NHG": fg_nhg_name}
        create_entry(config_db, FG_NHG_PREFIX, fg_nhg_prefix, fvs)

        asic_nh_count = len(asic_db.get_keys(ASIC_NH_TB))
        for i in range(0,NUM_NHs):
            if_name_key = "Ethernet" + str(i*4)
            ip_pref_key = "Ethernet" + str(i*4) + "|10.0.0." + str(i*2) + "/31"
            create_entry(config_db, IF_TB, if_name_key, fvs_nul)
            create_entry(config_db, IF_TB, ip_pref_key, fvs_nul)
            dvs.runcmd("config interface startup " + if_name_key)
            shutdown_link(dvs, app_db, i)
            startup_link(dvs, app_db, i)
            bank = 1
            if i >= NUM_NHs/2:
                bank = 0
            fvs = {"FG_NHG": fg_nhg_name, "bank": str(bank)}
            create_entry(config_db, FG_NHG_MEMBER, "10.0.0." + str(1 + i*2), fvs)
            ip_to_if_map["10.0.0." + str(1 + i*2)] = if_name_key
            dvs.runcmd("arp -s 10.0.0." + str(1 + i*2) + " 00:00:00:00:00:" + str(1 + i*2))

        asic_db.wait_for_n_keys(ASIC_NH_TB, asic_nh_count + NUM_NHs)

        # Program the route
        fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.1,10.0.0.11"),
            ("ifname", "Ethernet0,Ethernet20")])
        ps.set(fg_nhg_prefix, fvs)

        # Validate that the correct ASIC DB elements were setup per Fine Grained ECMP
        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = get_nh_oid_map(asic_db)

        # Test addition of route with 0 members in bank
        nh_memb_exp_count = {"10.0.0.1":64,"10.0.0.11":64}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Add 2 nhs to both bank 0 and bank 1
        nh_memb_exp_count = {"10.0.0.1":22,"10.0.0.3":21,"10.0.0.5":21,"10.0.0.11":22,
                "10.0.0.13":21,"10.0.0.15":21}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Add 2 more nhs to both bank 0 and bank 1
        nh_memb_exp_count = {"10.0.0.1":13,"10.0.0.3":13,"10.0.0.5":13,"10.0.0.7":12,
                "10.0.0.9":13,"10.0.0.11":13,"10.0.0.13":13,"10.0.0.15":13,"10.0.0.17":12,"10.0.0.19":13}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Remove 1 nh from bank 0 and remove 2 nhs from bank 1
        nh_memb_exp_count = {"10.0.0.3":16,"10.0.0.5":16,"10.0.0.7":16,"10.0.0.9":16,
                "10.0.0.11":22,"10.0.0.13":21,"10.0.0.19":21}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Remove 1 nh from bank 0 and add 1 nh to bank 1
        nh_memb_exp_count = {"10.0.0.3":22,"10.0.0.7":21,"10.0.0.9":21,"10.0.0.13":16,
                "10.0.0.15":16,"10.0.0.17":16,"10.0.0.19":16}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Remove 2 nh from bank 0 and remove 3 nh from bank 1
        nh_memb_exp_count = {"10.0.0.7":64,"10.0.0.11":64}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Add 2 nhs to bank 0 and remove all nh from bank 1
        nh_memb_exp_count = {"10.0.0.5":42,"10.0.0.7":44,"10.0.0.9":42}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Add 2 nhs to bank 0 and add 1 nh to bank 1
        nh_memb_exp_count = {"10.0.0.1":12,"10.0.0.3":13,"10.0.0.5":13,"10.0.0.7":13,
                "10.0.0.9":13,"10.0.0.11":64}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)
        
        fgnhg_clean_up(config_db, asic_db, app_db, state_db, fg_nhg_name, fg_nhg_prefix, 6)
        # clean up nh entries
        for i in range(0,NUM_NHs):
            if_name_key = "Ethernet" + str(i*4)
            ip_pref_key = "Ethernet" + str(i*4) + "|10.0.0." + str(i*2) + "/31"
            remove_entry(config_db, IF_TB, ip_pref_key)
            remove_entry(config_db, IF_TB, vlan_name_key)
            dvs.runcmd("config interface shutdown " + if_name_key)
            dvs.servers[i].runcmd("ip link set down dev eth0") == 0
            remove_entry(config_db, "FG_NHG_MEMBER", "10.0.0." + str(1 + i*2))

    def test_route_fgnhg_warm_reboot(self, dvs, testlog):
        dvs.runcmd("swssloglevel -l INFO -c orchagent")
        app_db = dvs.get_app_db()
        asic_db = dvs.get_asic_db()
        config_db = dvs.get_config_db()
        state_db = dvs.get_state_db()
        fvs_nul = {"NULL": "NULL"}
        NUM_NHs = 6
        fg_nhg_name = "fgnhg_v4"
        fg_nhg_prefix = "2.2.2.0/24"
        bucket_size = 60
        ip_to_if_map = {}

        fvs = {"bucket_size": str(bucket_size)}
        create_entry(config_db, FG_NHG, fg_nhg_name, fvs)

        fvs = {"FG_NHG": fg_nhg_name}
        create_entry(config_db, FG_NHG_PREFIX, fg_nhg_prefix, fvs)

        for i in range(0,NUM_NHs):
            if_name_key = "Ethernet" + str(i*4)
            vlan_name_key = "Vlan" + str((i+1)*4)
            ip_pref_key = vlan_name_key + "|10.0.0." + str(i*2) + "/31"
            fvs = {"vlanid": str((i+1)*4)}
            create_entry(config_db, VLAN_TB, vlan_name_key, fvs)
            fvs = {"tagging_mode": "untagged"}
            create_entry(config_db, VLAN_MEMB_TB, vlan_name_key + "|" + if_name_key, fvs)
            create_entry(config_db, VLAN_IF_TB, vlan_name_key, fvs_nul)
            create_entry(config_db, VLAN_IF_TB, ip_pref_key, fvs_nul)
            dvs.runcmd("config interface startup " + if_name_key)
            dvs.servers[i].runcmd("ip link set down dev eth0") == 0
            dvs.servers[i].runcmd("ip link set up dev eth0") == 0
            bank = 0
            if i >= NUM_NHs/2:
                bank = 1
            fvs = {"FG_NHG": fg_nhg_name, "bank": str(bank), "link": if_name_key}
            create_entry(config_db, FG_NHG_MEMBER, "10.0.0." + str(1 + i*2), fvs)
            ip_to_if_map["10.0.0." + str(1 + i*2)] = vlan_name_key

        # Wait for the software to receive the entries
        time.sleep(1)

        ps = swsscommon.ProducerStateTable(app_db.db_connection, ROUTE_TB)
        fvs = swsscommon.FieldValuePairs([("nexthop","10.0.0.7,10.0.0.9,10.0.0.11"),
            ("ifname", "Vlan16,Vlan20,Vlan24")])
        ps.set(fg_nhg_prefix, fvs)
        # No ASIC_DB entry we can wait for since ARP is not resolved yet,
        # We just use sleep so that the sw receives this entry
        time.sleep(1)

        asic_nh_count = len(asic_db.get_keys(ASIC_NH_TB))
        dvs.runcmd("arp -s 10.0.0.1 00:00:00:00:00:01")
        dvs.runcmd("arp -s 10.0.0.3 00:00:00:00:00:02")
        dvs.runcmd("arp -s 10.0.0.5 00:00:00:00:00:03")
        dvs.runcmd("arp -s 10.0.0.7 00:00:00:00:00:04")
        dvs.runcmd("arp -s 10.0.0.9 00:00:00:00:00:05")
        dvs.runcmd("arp -s 10.0.0.11 00:00:00:00:00:06")
        asic_db.wait_for_n_keys(ASIC_NH_TB, asic_nh_count + 6)

        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = get_nh_oid_map(asic_db)

        # Now that ARP was resolved, 10.0.0.7 should be added as a valid fg nhg member
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        
        run_warm_reboot(dvs)

        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = get_nh_oid_map(asic_db)

        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        validate_fine_grained_asic_n_state_db_entries(asic_db, state_db, ip_to_if_map,
                                nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.7":30,"10.0.0.11":30}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring down 2 next hop and bring up 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.9":60}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring up 1 next hop in bank 1
        nh_memb_exp_count = {"10.0.0.7":20,"10.0.0.9":20,"10.0.0.11":20}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        # Bring up some next-hops in bank 0 for the 1st time
        nh_memb_exp_count = {"10.0.0.1":10,"10.0.0.3":10,"10.0.0.5":10,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)

        run_warm_reboot(dvs)

        asic_db.wait_for_n_keys(ASIC_NHG_MEMB, bucket_size)
        nhgid = validate_asic_nhg_fine_grained_ecmp(asic_db, fg_nhg_prefix, bucket_size)

        nh_oid_map = get_nh_oid_map(asic_db)

        nh_memb_exp_count = {"10.0.0.1":10,"10.0.0.3":10,"10.0.0.5":10,"10.0.0.7":10,"10.0.0.9":10,"10.0.0.11":10}
        program_route_and_validate_fine_grained_ecmp(app_db.db_connection, asic_db, state_db, ip_to_if_map,
                            fg_nhg_prefix, nh_memb_exp_count, nh_oid_map, nhgid, bucket_size)
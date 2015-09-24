#!/usr/bin/python3
import os
import sys
import time
import re
import dbus
import pwd
import time
import socket
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BUS_NAME="org.freedesktop.problems"

DBUS_ERROR_BAD_ADDRESS="org.freedesktop.DBus.Error.BadAddress: Requested Entry does not exist"
DBUS_ERROR_ACCESS_DENIED_READ="org.freedesktop.DBus.Error.AccessDenied: You are not authorized to access the problem"
DBUS_ERROR_ACCESS_DENIED_DELETE="org.freedesktop.DBus.Error.AccessDenied: You are not authorized to delete the problem"

class TestFrame(object):

    def __init__(self, non_root_uid):
        DBusGMainLoop(set_as_default=True)

        self.loop = GLib.MainLoop()

        self.root_bus = dbus.SystemBus(private=True)
        self.root_p2_proxy = self.root_bus.get_object(BUS_NAME, '/org/freedesktop/Problems2')
        self.root_p2 = dbus.Interface(self.root_p2_proxy, dbus_interface='org.freedesktop.Problems2')

        os.seteuid(non_root_uid)

        self.bus = dbus.SystemBus(private=True)
        self.p2_proxy = self.bus.get_object(BUS_NAME, '/org/freedesktop/Problems2')
        self.p2 = dbus.Interface(self.p2_proxy, dbus_interface='org.freedesktop.Problems2')

        self.ac_signal_occurrences = []

    def interrupt_waiting(self, emergency=True):
        self.loop.quit()
        if not emergency:
            GLib.Source.remove(self.tm)

    def handle_authorization_changed(self, status):
        if not "AuthorizationChanged" in self.signals:
            return

        self.interrupt_waiting(False)
        self.ac_signal_occurrences.append(status)

    def handle_crash(self, entry_path, uid):
        if not "Crash" in self.signals:
            return

        self.interrupt_waiting(False)
        self.crash_signal_occurrences.append((entry_path, uid))

    def wait_for_signals(self, signals):
        self.signals = signals
        self.tm = GLib.timeout_add(1000, self.interrupt_waiting)
        self.loop.run()


def expect_dbus_error(error, method, *args):
    try:
        method(*args)
        print("FAILURE: Expected D-Bus error: %s" % (error))
    except dbus.exceptions.DBusException as ex:
        if str(ex) != error:
            print("FAILURE: caught invalid text:\n\tExpected: %s\n\tGot     :%s\n" % (error, str(ex)))


def dictionary_key_has_value(dictionary, key, expected):
    if not key in dictionary:
        print("FAILURE: missing '%s'" % (key))
    elif dictionary[key] != expected:
        print("FAILURE: key '%s', expected: '%s', is:'%s'", key, expected, dictionary[key])


def test_fake_binary_type(tf):
    print("TEST FAKE BINARY TYPE")

    with open("/tmp/fake_type", "w") as type_file:
        type_file.write("CCpp")

    with open("/tmp/fake_type", "r") as type_file:
        description = {"analyzer"    : "problems2testsuite_analyzer",
                       "type"        : "problems2testsuite_type",
                       "reason"      : "Application has been killed",
                       "backtrace"   : "die()",
                       "executable"  : "/usr/bin/foo",
                       "type"        : dbus.types.UnixFd(type_file)}

        expect_dbus_error("org.freedesktop.problems.Failure: You are not allowed to create element 'type' containing 'CCpp'",
                              tf.p2.NewProblem, description)


def test_real_problem(tf):
    print("TEST REAL PROBLEM")

    # 25 * 41 = 1025
    data = "ABRT test case huge file " * 41
    with open("/tmp/hugetext", "w") as hugetext_file:
        # 9000KiB > 8MiB
        for i in range(0, 9000):
            hugetext_file.write(data)

    with open("/tmp/hugetext", "r") as hugetext_file:
        with open("/usr/bin/true", "r") as bintrue_file:
            description = {"analyzer"    : "problems2testsuite_analyzer",
                           "type"        : "problems2testsuite_type",
                           "reason"      : "Application has been killed",
                           "backtrace"   : "die()",
                           "executable"  : "/usr/bin/foo",
                           "uuid"        : "0123456789ABCDEF",
                           "duphash"     : "FEDCBA9876543210",
                           "package"     : "problems2-1.2-3",
                           "pkg_name"    : "problems2",
                           "pkg_version" : "1.2",
                           "pkg_release" : "3",
                           "cmdline"     : "/usr/bin/foo --blah",
                           "component"   : "abrt",
                           "reported_to" : "ABRT Server: BTHASH=0123456789ABCDEF MSG=test\nServer: URL=http://example.org\nServer: URL=http://case.org\n",
                           "hugetext"    : dbus.types.UnixFd(hugetext_file),
                           "binary"      : dbus.types.UnixFd(bintrue_file)}

            tf.problem_first_occurrence = time.time()
            tf.problem_id = tf.p2.NewProblem(description)
            if not tf.problem_id:
                print("FAILURE : empty return value")


def test_crash_signal(tf):
    print("TEST CRASH SIGNAL")

    if len(tf.crash_signal_occurrences) != 1:
        print("FAILURE : Crash signal wasn't emitted")
    else:
        if tf.crash_signal_occurrences[0][0] != tf.problem_id:
            print("FAILURE : Crash signal was emitted with wrong PATH")
        if tf.crash_signal_occurrences[0][1] != os.geteuid():
            print("FAILURE : Crash signal was emitted with wrong UID")


def test_get_problems(tf):
    print("TEST GET PROBLEMS")

    p = tf.p2.GetProblems()
    if not p:
        print("FAILURE: no problems")

    if not tf.problem_id in p:
        print("FAILURE: missing our problem")

    if tf.private_problem_id in p:
        print("FAILURE: contains private problem")


def test_get_problem_data(tf):
    print("TEST GET PROBLEM DATA")

    #tf.p2.GetProblemData(dbus.types.String())

    expect_dbus_error(DBUS_ERROR_BAD_ADDRESS,
        tf.p2.GetProblemData, "/invalid/path")

    expect_dbus_error(DBUS_ERROR_BAD_ADDRESS,
        tf.p2.GetProblemData, "/org/freedesktop/Problems2/Entry/FAKE")

    expect_dbus_error(DBUS_ERROR_ACCESS_DENIED_READ,
            tf.p2.GetProblemData, tf.private_problem_id)

    p = tf.p2.GetProblemData(tf.problem_id)
    expected = {
        "analyzer"    : (2, len("problems2testsuite_analyzer"), "problems2testsuite_analyzer"),
        "type"        : (2, len("problems2testsuite_type"), "problems2testsuite_type"),
        "reason"      : (2, len("Application has been killed"), "Application has been killed"),
        "backtrace"   : (2, len("die()"), "die()"),
        "executable"  : (2, len("/usr/bin/foo"), "/usr/bin/foo"),
        "hugetext"    : (64, os.path.getsize("/tmp/hugetext"), "/var/spool/abrt/[^/]+/hugetext"),
        "binary"      : (1, os.path.getsize("/usr/bin/true"), "/var/spool/abrt/[^/]+/binary"),
    }

    for k, v in expected.items():
        if not k in p:
            print("FAILURE: missing " + k)
            continue

        g = p[k]
        if not re.match(v[2], g[2]):
            print("FAILURE: invalid contents of '%s'" % (k))

        if g[1] != v[1]:
            print("FAILURE: invalid length '%s' : %i" % (k, g[1]))

        if (g[0] & v[0]) != v[0]:
            print("FAILURE: invalid flags %s : %i" % (k, g[0]))


def test_get_private_problem(tf):
    print("TEST GET PRIVATE PROBLEM")

    p = tf.p2.GetProblems()
    if not p:
        print("FAILURE: no problems")

    if not tf.problem_id in p:
        print("FAILURE: missing our problem")

    if not tf.private_problem_id in p:
        print("FAILURE: missing private problem")

    p = tf.p2.GetProblemData(tf.private_problem_id)

    if p["uid"][2] != "0":
        print("FAILURE: invalid UID")


def test_private_problem_not_accessible(tf):
    print("TEST PRIVATE PROBLEM NOT ACCESSIBLE WHEN SESSION IS CLOSED")

    p = tf.p2.GetProblems()
    if not p:
        print("FAILURE: no problems")

    if not tf.problem_id in p:
        print("FAILURE: missing our problem")

    if tf.private_problem_id in p:
        print("FAILURE: accessible private problem")

    expect_dbus_error(DBUS_ERROR_ACCESS_DENIED_READ,
            tf.p2.GetProblemData, tf.private_problem_id)

    expect_dbus_error(DBUS_ERROR_ACCESS_DENIED_DELETE,
            tf.p2.DeleteProblems, [tf.private_problem_id])


def test_delete_problems(tf):
    print("TEST DELETE PROBLEMS")

    description = {"analyzer"    : "problems2testsuite_analyzer",
                   "type"        : "problems2testsuite_type",
                   "reason"      : "Application has been killed",
                   "backtrace"   : "die()",
                   "executable"  : "/usr/bin/sh",
                   "duphash"     : None,
                   "uuid"        : None}

    description["duphash"] = description["uuid"] = "DEADBEEF"
    one = tf.p2.NewProblem(description)

    description["duphash"] = description["uuid"] = "81680083"
    two = tf.p2.NewProblem(description)

    description["duphash"] = description["uuid"] = "FFFFFFFF"
    three = tf.p2.NewProblem(description)

    p = tf.p2.GetProblems()
    if not(one in p and two in p and three in p):
        print("FAILURE: problems not detected")

    tf.p2.DeleteProblems([one])

    p = tf.p2.GetProblems()
    if one in p:
        print("FAILURE: 'one' not removed")

    if not(two in p and three in p):
        print("FAILURE: 'two' and 'three' disappeared")

    expect_dbus_error(DBUS_ERROR_BAD_ADDRESS,
            tf.p2.DeleteProblems, [two, three, one])

    p = tf.p2.GetProblems()
    if two in p and three in p:
        print("FAILURE: 'two' and 'three' not removed")

    tf.p2.DeleteProblems([])

    expect_dbus_error(DBUS_ERROR_BAD_ADDRESS,
            tf.p2.DeleteProblems, ["/invalid/path"])

    expect_dbus_error(DBUS_ERROR_BAD_ADDRESS,
            tf.p2.DeleteProblems, ["/org/freedesktop/Problems2/Entry/FAKE"])

    expect_dbus_error(DBUS_ERROR_ACCESS_DENIED_DELETE,
            tf.p2.DeleteProblems, [tf.private_problem_id])


def test_get_session(tf):
    print("TEST GET SESSION")

    p2_session_obj = tf.p2.GetSession()
    if p2_session_obj != "/org/freedesktop/Problems2/Session/1":
        print("FAILURE : wrong session path : %s" % (str(p2_session_obj)))

    tf.p2_session_proxy = tf.bus.get_object(BUS_NAME, p2_session_obj)
    tf.p2_session_props = dbus.Interface(tf.p2_session_proxy, dbus_interface=dbus.PROPERTIES_IFACE)
    tf.p2_session = dbus.Interface(tf.p2_session_proxy, dbus_interface='org.freedesktop.Problems2.Session')

    if tf.p2_session_props.Get(tf.p2_session.dbus_interface, "is_authorized"):
        print("FAILURE : is authorized by default")
    return False


def test_authrorize(tf):
    print("TEST AUTHORIZE")

    if tf.p2_session.Authorize(dbus.types.String(), 1) != 0:
        print("FAILURE : cannot authorize")

    if not tf.p2_session_props.Get(tf.p2_session.dbus_interface, "is_authorized"):
        print("FAILURE : not authorized")


def test_authrorize_signal(tf):
    print("TEST AUTHORIZE SIGNAL")

    if len(tf.ac_signal_occurrences) != 1:
        print("FAILURE : signal wasn't emitted")

    if len(tf.ac_signal_occurrences) == 1 and tf.ac_signal_occurrences[0] != 0:
        print("FAILURE : signal was emitted with wrong number")


def test_close(tf):
    print("TEST CLOSE")

    tf.p2_session.Close()

    p2_session_obj = tf.p2.GetSession()
    tf.p2_session_proxy = tf.bus.get_object(BUS_NAME, p2_session_obj)
    tf.p2_session_props = dbus.Interface(tf.p2_session_proxy, dbus_interface=dbus.PROPERTIES_IFACE)
    tf.p2_session = dbus.Interface(tf.p2_session_proxy, dbus_interface='org.freedesktop.Problems2.Session')

    if tf.p2_session_props.Get(tf.p2_session.dbus_interface, "is_authorized"):
        print("FAILURE : still authorized")


def test_close_signal(tf):
    print("TEST CLOSE SIGNAL")

    if len(tf.ac_signal_occurrences) != 2:
        print("FAILURE : signal wasn't emitted")

    if len(tf.ac_signal_occurrences) == 2 and tf.ac_signal_occurrences[1] != 1:
        print("FAILURE : signal was emitted with wrong number")


def create_problem(p2):
    with open("/usr/bin/true", "r") as bintrue_file:
        description = {"analyzer"    : "problems2testsuite_analyzer",
                       "type"        : "problems2testsuite_type",
                       "reason"      : "Application has been killed",
                       "backtrace"   : "die()",
                       "executable"  : "/usr/bin/foo",}

        return p2.NewProblem(description)


class Problems2Entry(object):

    def __init__(self, bus, entry_path):
        entry_proxy = bus.get_object(BUS_NAME, entry_path)
        self._properties = dbus.Interface(entry_proxy, dbus_interface="org.freedesktop.DBus.Properties")
        self._entry = dbus.Interface(entry_proxy, dbus_interface="org.freedesktop.Problems2.Entry")

    def __getattribute__(self, name):
        try:
            return object.__getattribute__(self, name)
        except AttributeError as ex:
            entry = object.__getattribute__(self, "_entry")
            return entry.get_dbus_method(name)

    def getproperty(self, name):
        properties = object.__getattribute__(self, "_properties")
        return properties.Get("org.freedesktop.Problems2.Entry", name)


def test_problem_entry_properties(tf):
    print("TEST ELEMENTARY ENTRY PROPERTIES")

    p2e = Problems2Entry(tf.bus, tf.problem_id)

    if not re.match("/var/spool/abrt/problems2testsuite_type[^/]*", p2e.getproperty("id")):
        print("FAILURE: strange problem ID")

    if p2e.getproperty("user") != pwd.getpwuid(os.geteuid()).pw_name:
        print("FAILURE: strange username")

    if p2e.getproperty("hostname") != socket.gethostname():
        print("FAILURE: invalid hostname")

    if p2e.getproperty("type") != "problems2testsuite_type":
        print("FAILURE: invalid type")

    if p2e.getproperty("executable") != "/usr/bin/foo":
        print("FAILURE: invalid executable")

    if p2e.getproperty("command_line_arguments") != "/usr/bin/foo --blah":
        print("FAILURE: invalid command_line_arguments")

    if p2e.getproperty("component") != "abrt":
        print("FAILURE: invalid component")

    if p2e.getproperty("duphash") != "FEDCBA9876543210":
        print("FAILURE: invalid duphash")

    if p2e.getproperty("uuid") != "0123456789ABCDEF":
        print("FAILURE: invalid uuid")

    if p2e.getproperty("reason") != "Application has been killed":
        print("FAILURE: invalid reason")

    if p2e.getproperty("uid") != os.geteuid():
        print("FAILURE: strange uid")

    if p2e.getproperty("count") != 1:
        print("FAILURE: count is not 1")

    if abs(p2e.getproperty("first_occurrence") - tf.problem_first_occurrence) >= 5:
        print("FAILURE: too old first occurrence")

    if p2e.getproperty("first_occurrence") != p2e.getproperty("last_occurrence"):
        print("FAILURE: first_occurrence and last_occurrence differ")

    if abs(p2e.getproperty("last_occurrence") - tf.problem_first_occurrence) >= 5:
        print("FAILURE: too old last occurrence")

    if not p2e.getproperty("is_reported"):
        print("FAILURE: 'is_reported' == FALSE but should be reported")

    if not p2e.getproperty("can_be_reported"):
        print("FAILURE: 'cannot be reported' but should be report-able")

    if p2e.getproperty("is_remote"):
        print("FAILURE: 'is_remote' but should not be remote")

    package = p2e.getproperty("package")
    if len(package) != 5:
        print("FAILURE: insufficient number of package members")

    if package != ("problems2-1.2-3", "", "problems2", "1.2", "3"):
        print("FAILURE: invalid package struct %s" % (str(package)))

    elements = p2e.getproperty("elements")
    if len(elements) == 0:
        print("FAILURE: insufficient number of elements")

    for e in ["analyzer", "type", "reason", "backtrace", "executable", "uuid",
              "duphash", "package", "pkg_name", "pkg_version", "pkg_release",
              "cmdline", "component", "hugetext", "binary", "count", "time"]:

        if not e in elements:
            print("FAILURE: missing element %s" % (e))

    reports = p2e.getproperty("reports")
    if len(reports) != 3:
        print("FAILURE: missing some reports")

    exp = [
        ("ABRT Server", { "BTHASH" : "0123456789ABCDEF", "MSG" : "test"}),
        ("Server", { "URL" : "http://example.org"}),
        ("Server", { "URL" : "http://case.org"}),
        ]

    for i in range(0, len(e) - 1):
        if exp[i][0] != reports[i][0]:
            print("FAILURE: invalid label %d, %s" % (i, reports[i][0]))

        if exp[i][1] != reports[i][1]:
            print("FAILURE: invalid value %d, %s" % (i, str(reports[i][1])))


def test_read_elements(tf):
    print("TEST READ ELEMENTS")

    requested = { "reason" : dbus.types.String,
                  "hugetext" : dbus.types.UnixFd,
                  "binary" : dbus.types.UnixFd }

    p2e = Problems2Entry(tf.bus, tf.problem_id)
    elements = p2e.ReadElements(requested.keys(), 0)

    for r, t in requested.items():
        if not r in elements:
            print("FAILURE: response is missing %s" % (r))
            continue

        if type(elements[r]) != t:
            print("FAILURE: invalid type of %s: %s" % (r, str(type(elements[r]))))

    resp = p2e.ReadElements([], 0x0)
    if len(resp) != 0:
        print("FAILURE: the response for an empty request is not empty")

    resp = p2e.ReadElements(["foo"], 0x0)
    if len(resp) != 0:
        print("FAILURE: the response for an request with non-existing element is not empty")

    resp = p2e.ReadElements(["/etc/shadow", "../../../../etc/shadow"], 0x0)
    if len(resp) != 0:
        print("FAILURE: the response for an request with prohibited elements is not empty")

    reasonlist = p2e.ReadElements(["reason"], 0x08)
    if reasonlist:
        print("FAILURE: returned text when ONLY_BIG_TEXT requested")

    reasonlist = p2e.ReadElements(["reason"], 0x10)
    if reasonlist:
        print("FAILURE: returned text when ONLY_BIN requested")

    reasonlist = p2e.ReadElements(["reason"], 0x04)
    if len(reasonlist) != 1:
        print("FAILURE: not returned text when ONLY_TEXT requested")

    if reasonlist["reason"] != "Application has been killed":
        print("FAILURE: invalid data returned")

    reasonlist = p2e.ReadElements(["reason"], 0x01 | 0x04)
    if len(reasonlist) != 1:
        print("FAILURE: not returned fd when ALL_FD | ONLY_TEXT requested")

    fd = reasonlist["reason"].take()

    # try read few more bytes to verify that the file is not broken
    data = os.read(fd, len("Application has been killed") + 10)
    if "Application has been killed" != data.decode():
        print("FAILURE: invalid data read from file descriptor : '%s'" % (data))
    os.close(fd)


def test_save_elements(tf):
    print("TEST SAVE ELEMENTS")

    p2e = Problems2Entry(tf.bus, tf.problem_id)

    shorttext = "line one\nline two\nline three\n"
    with open("/tmp/shorttext", "w") as shorttext_file:
        shorttext_file.write(shorttext)

    with open("/tmp/shorttext", "r") as fstab_file:
        request = { "random" : "random text",
                    "shorttext" : dbus.types.UnixFd(fstab_file) }

        dummy = p2e.SaveElements(request, 0)

    elements = p2e.getproperty("elements")
    if not "random" in elements or not "shorttext" in elements:
        print("FAILURE: property 'elements' does not include the created elements")

    resp = p2e.ReadElements(["random", "shorttext"], 0x00)
    if len(resp) != 2:
        print("FAILURE: not returned both requested elements")

    dictionary_key_has_value(resp, "random", "random text")
    dictionary_key_has_value(resp, "shorttext", shorttext)

    resp = p2e.SaveElements(dict(), 0x0)

    for path in ["/tmp/shadow", "/tmp/passwd"]:
        try:
            os.unlink(path)
        except OSError:
            pass

    resp = p2e.SaveElements({"/tmp/shadow" : "blah", "../../../../tmp/passwd" : "bar"}, 0x0)

    try:
        os.unlink("/tmp/shadow")
        print("FAILURE: accepted an absolute path")
    except OSError:
        pass

    try:
        os.unlink("/tmp/passwd")
        print("FAILURE: accepted a relative path")
    except OSError:
        pass


def test_delete_elements(tf):
    print("TEST DELETE ELEMENTS")

    p2e = Problems2Entry(tf.bus, tf.problem_id)

    deleted_elements = { "delete_one" : "delete one",
                         "delete_two" : "delete two",
                         "delete_six" : "delete six" }

    p2e.SaveElements(deleted_elements, 0x0)
    elements = p2e.getproperty("elements")
    for e in deleted_elements.keys():
        if not e in elements:
            print("FAILURE: element does not exist: %s" % (e))

    p2e.DeleteElements(["delete_one"])
    elements = p2e.getproperty("elements")
    if "delete_one" in elements:
        print("FAILURE: 'delete_one' has not been removed")
    if not "delete_two" in elements or not "delete_six" in elements:
        print("FAILURE: the other elements have disappeared")

    p2e.DeleteElements(["delete_one", "delete_two", "delete_six"])
    elements = p2e.getproperty("elements")
    if "delete_two" in elements or "delete_six" in elements:
        print("FAILURE: the other elements have not been removed")

    p2e.DeleteElements([])

    for path in ["/tmp/shadow", "/tmp/passwd"]:
        with open(path, "w") as tmp_file:
            tmp_file.write("should not be touched")

    resp = p2e.DeleteElements(["/tmp/shadow", "../../../../tmp/passwd"])

    try:
        os.unlink("/tmp/shadow")
    except OSError as ex:
        print ("FAILURE: removed an absolute path: %s" % (str(ex)))

    try:
        os.unlink("/tmp/passwd")
    except OSError as ex:
        print ("FAILURE: removed a relative path: %s" % (str(ex)))


if __name__ == "__main__":
    if os.getuid() != 0:
        print("Run this test under root!")
        sys.exit(1)

    if len(sys.argv) != 2:
        print("Pass an uid of non-root user as the first argument!")
        sys.exit(1)

    non_root_uid = int(sys.argv[1])
    tf = TestFrame(non_root_uid)

    test_fake_binary_type(tf)

    tf.crash_signal_occurrences = []
    tf.p2.connect_to_signal("Crash", tf.handle_crash)

    test_real_problem(tf)
    tf.wait_for_signals(["Crash"])

    tf.private_problem_id = create_problem(tf.root_p2)

    test_get_problems(tf)
    test_get_problem_data(tf)
    test_delete_problems(tf)
    test_get_session(tf)

    tf.ac_signal_occurrences = []
    tf.p2_session.connect_to_signal("AuthorizationChanged", tf.handle_authorization_changed)

    test_authrorize(tf)

    tf.wait_for_signals(["AuthorizationChanged"])

    test_authrorize_signal(tf)
    test_get_private_problem(tf)
    test_close(tf)

    tf.wait_for_signals(["AuthorizationChanged"])

    test_close_signal(tf)
    test_private_problem_not_accessible(tf)
    test_problem_entry_properties(tf)
    test_read_elements(tf)
    test_save_elements(tf)
    test_delete_elements(tf)

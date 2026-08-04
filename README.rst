===================================================
Octavia Tempest Plugin (containerised-compute fork)
===================================================

A fork of ``openstack/octavia-tempest-plugin`` that runs against a cloud
whose Nova driver produces **system containers rather than virtual
machines** -- specifically the Incus driver used by the
``incus-octavia-provider`` load balancer provider.

Upstream is the authority on what these tests assert. This fork changes
only how a few of them talk to the guest, never what they check.

Why a fork
----------

Several helpers in the scenario suite were written for cirros on a 64MB
flavor and assume a virtual machine underneath. On an unprivileged
container they fail in ``setUpClass``, before any load balancer is
exercised -- and because they sit in one function with no exception
handling, each failure hides the next. Upstream's own comments describe
two of them as workarounds to be removed once their premise expires.

What differs from upstream
--------------------------

Four changes, each removing a workaround whose premise does not hold
here, or fixing a defect that is not container-specific at all:

1. **The ``vm.overcommit_memory`` write is gone.** Upstream raises it so
   a golang static binary can reserve address space on the 64MB nano
   flavor, and carries a ``TODO`` asking for the call to be removed. The
   flavors here are 512MB and 1GB, so the premise is gone; and the sysctl
   is not namespaced, so inside a container the write fails at any
   privilege level -- there is no per-container knob to grant, and
   succeeding would mean changing the host.

2. **The test server is backgrounded with ``setsid`` instead of
   ``screen``.** Ubuntu cloud images do not carry ``screen``; ``setsid``
   is in coreutils and always present. The same binary starts with the
   same arguments. Installing ``screen`` instead is not available: the
   suite never passes ``user_data`` or a config drive, so there is no
   provisioning hook.

3. **The guest configures its own IPv6 address.** Upstream adds it by
   hand because cirros does not; Ubuntu cloud images do, via cloud-init
   and netplan, before any test runs. The manual step also names
   ``eth0``, which an Incus container does not have -- its interfaces are
   ``nic<hash>`` -- and even addressed correctly it returns "address
   already assigned". A guest that genuinely does not self-configure IPv6
   would need this back, matched by MAC rather than by device name.

4. **UDP requests always have a receive deadline.** ``make_udp_request``
   skipped ``settimeout`` when no timeout was given, so ``recvfrom``
   blocked forever after a single datagram with no retry. Callers reach
   it from ``setUpClass``, where tempest's per-test timeout fixture
   cannot intervene, so one dropped datagram hung the whole run instead
   of failing a test. This one is not container-specific and would hang
   the same way on a hypervisor.

None of these weakens an assertion. That line is what keeps results from
this fork readable: remove workarounds whose premise no longer holds,
never change what is being checked.

Reporting results
-----------------

Any conformance number obtained with this fork must be reported as such,
with the divergence above stated. It is not a run of upstream
octavia-tempest-plugin.

Tests that still do not run here
--------------------------------

Removing the above does not make the whole suite applicable. Known
remaining gaps, none of which this fork papers over:

* ``act_stdby_scenario`` writes ``nf_log_all_netns``, a sysctl registered
  only for the initial network namespace. It is namespace-gated rather
  than permission-gated, so no privilege level fixes it; the mechanism
  itself -- iptables ``LOG`` targets read back out of the kernel ring
  buffer -- would have to change. These tests are skipped for other
  reasons on a non-amphora provider anyway.

Upstream
--------

.. image:: https://governance.openstack.org/tc/badges/octavia-tempest-plugin.svg

Tempest integration of Octavia
------------------------------

This project contains the Tempest plugin for the Octavia project for
OpenStack Load Balancing.

For more information about Octavia see:
https://docs.openstack.org/octavia/latest/

For more information about Tempest plugins see:
https://docs.openstack.org/tempest/latest/plugin.html

* Free software: Apache license
* Documentation: https://docs.openstack.org/octavia-tempest-plugin/latest/
* Source: https://opendev.org/openstack/octavia-tempest-plugin
* Bugs: https://bugs.launchpad.net/octavia

Installing
----------

From the tempest directory, setup the tempest virtual environment for the
Octavia tempest plugin::

    $ tox -e venv-tempest -- pip3 install -e <path to octavia-tempest-plugin>

For example, when using a typical devstack setup::

    $ cd /opt/stack/tempest
    $ tox -e venv-tempest -- pip3 install -e /opt/stack/octavia-tempest-plugin

Running the tests
-----------------

To run all the tests from this plugin, call from the tempest repo::

    $ tox -e all -- octavia_tempest_plugin

To run a single test case, call with full path, for example::

    $ tox -e all -- octavia_tempest_plugin.tests.scenario.v2.test_traffic_ops.TrafficOperationsScenarioTest.test_basic_traffic

To retrieve a list of all tempest tests, run::

    $ testr list-tests

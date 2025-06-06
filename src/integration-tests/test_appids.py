# Copyright 2024 Bloomberg Finance L.P.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
from typing import List

import blazingmq.dev.it.testconstants as tc
from blazingmq.dev.it.fixtures import (  # pylint: disable=unused-import
    Cluster,
    cluster,
    test_logger,
    order,
    multi_node,
    tweak,
)
from blazingmq.dev.it.process.client import Client
from blazingmq.dev.it.util import attempt, wait_until

pytestmark = order(3)

default_app_ids = ["foo", "bar", "baz"]
timeout = 10
max_msgs = 3

set_max_messages = tweak.domain.storage.queue_limits.messages(max_msgs)


def set_app_ids(cluster: Cluster, app_ids: List[str], du: tc.DomainUrls):  # noqa: F811
    cluster.config.domains[
        du.domain_fanout
    ].definition.parameters.mode.fanout.app_ids = app_ids  # type: ignore
    cluster.reconfigure_domain(du.domain_fanout, succeed=True)


def test_open_alarm_authorize_post(cluster: Cluster, domain_urls: tc.DomainUrls):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    all_app_ids = default_app_ids + ["quux"]

    # ---------------------------------------------------------------------
    # Create a consumer for each authorized substream.

    consumers = {}

    for app_id in default_app_ids:
        consumer = next(proxies).create_client(app_id)
        consumers[app_id] = consumer
        consumer.open(f"{du.uri_fanout}?id={app_id}", flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # Create a consumer for the unauthorized substream. This should succeed
    # but with an ALARM.

    quux = next(proxies).create_client("quux")
    consumers["quux"] = quux
    assert (
        quux.open(f"{du.uri_fanout}?id=quux", flags=["read"], block=True)
        == Client.e_SUCCESS
    )
    assert leader.alarms()

    # ---------------------------------------------------------------------
    # Check that authorized substreams are alive and 'quux' is unauthorized.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)

    bar_status, baz_status, foo_status, quuxStatus = sorted(
        [
            leader.capture(r"(\w+).*: status=(\w+)(?:, StorageIter.atEnd=(\w+))?", 60)
            for i in all_app_ids
        ],
        key=lambda match: match[1],
    )
    assert bar_status[2] == "alive"
    assert baz_status[2] == "alive"
    assert foo_status[2] == "alive"
    assert quuxStatus.group(2, 3) == ("unauthorized", None)

    assert (
        quux.configure(
            f"{du.uri_fanout}?id=quux", max_unconfirmed_messages=10, block=True
        )
        == Client.e_SUCCESS
    )

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(du.uri_fanout, ["msg1"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # Check that 'quux' (unauthorized) client did not receive it.
    test_logger.info('Check that "quux" has not seen any messages')
    assert not quux.wait_push_event(timeout=2, quiet=True)
    assert len(quux.list(f"{du.uri_fanout}?id=quux", block=True)) == 0

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(cluster, default_app_ids + ["quux"], du)

    # ---------------------------------------------------------------------
    # Check that all substreams are alive.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)

    for app_id in all_app_ids:
        test_logger.info(f"Check that {app_id} is alive")
        leader.outputs_regex(r"(\w+).*: status=alive", timeout)

    # ---------------------------------------------------------------------
    # Post a second message.

    producer.post(du.uri_fanout, ["msg2"])
    assert producer.outputs_regex(r"MESSAGE.*ACK", timeout)

    # ---------------------------------------------------------------------
    # Ensure that previously authorized substreams get 2 messages and the
    # newly authorized gets one.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
    # pylint: disable=cell-var-from-loop; passing lambda to 'wait_until' is safe
    for app_id in default_app_ids:
        test_logger.info(f"Check if {app_id} has seen 2 messages")
        assert wait_until(
            lambda: len(
                consumers[app_id].list(f"{du.uri_fanout}?id={app_id}", block=True)
            )
            == 2,
            3,
        )

    test_logger.info("Check if quux has seen 1 message")
    assert wait_until(
        lambda: len(quux.list(f"{du.uri_fanout}?id=quux", block=True)) == 1, 3
    )

    for app_id in all_app_ids:
        assert (
            consumers[app_id].close(f"{du.uri_fanout}?id={app_id}", block=True)
            == Client.e_SUCCESS
        )

    # Start the 'quux' consumer and then ensure that no alarm is raised at
    # leader/primary when a consumer for a recently authorized appId is
    # stopped and started.

    quux.open(f"{du.uri_fanout}?id=quux", flags=["read"], succeed=True)
    assert not leader.alarms()


def test_create_authorize_open_post(cluster: Cluster, domain_urls: tc.DomainUrls):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write"], succeed=True)

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(cluster, default_app_ids + ["quux"], du)

    # ---------------------------------------------------------------------
    # Create a consumer for 'quux. This should succeed.

    quux = next(proxies).create_client("quux")
    assert (
        quux.open(f"{du.uri_fanout}?id=quux", flags=["read"], block=True)
        == Client.e_SUCCESS
    )

    # ---------------------------------------------------------------------
    # Check that all substreams are alive.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
    leader.outputs_regex(r"quux.*: status=alive", timeout)


def test_load_domain_authorize_open_post(cluster: Cluster, domain_urls: tc.DomainUrls):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout + "_another", flags=["write"], succeed=True)

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(cluster, default_app_ids + ["quux"], du)

    # ---------------------------------------------------------------------
    # Create a consumer for 'quux. This should succeed.

    quux = next(proxies).create_client("quux")
    quux.open(f"{du.uri_fanout}?id=quux", flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # Check that all substreams are alive.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
    leader.outputs_regex(r"quux.*: status=alive", timeout)


# following test cannot run yet, because domain manager claims domain
# does not exist if no queue exists in it
def _test_authorize_before_domain_loaded(cluster, domain_urls: tc.DomainUrls):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(cluster, default_app_ids + ["quux"], du)

    # ---------------------------------------------------------------------
    # Create the queue.

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write"], succeed=True)

    # ---------------------------------------------------------------------
    # Create a consumer for quux. This should succeed.

    quux = next(proxies).create_client("quux")
    quux.open(f"{du.uri_fanout}?id=quux", flags=["read"])
    assert quux.outputs_regex(r"openQueue.*\[SUCCESS\]", timeout)

    # ---------------------------------------------------------------------
    # Check that all substreams are alive.

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
    leader.outputs_regex(r"quux.*: status=alive", timeout)


# following test cannot run yet, because domain manager claims domain
# does not exist if no queue exists in it
def _test_command_errors(cluster, domain_urls: tc.DomainUrls):
    proxies = cluster.proxy_cycle()
    next(proxies).create_client("producer")

    set_app_ids(cluster, default_app_ids + ["quux"], domain_urls)

    set_app_ids(cluster, default_app_ids, domain_urls)


def test_unregister_in_presence_of_queues(cluster: Cluster, domain_urls: tc.DomainUrls):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    producer.post(du.uri_fanout, ["before-unregister"], block=True)
    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)

    foo = next(proxies).create_client("foo")
    foo.open(du.uri_fanout_foo, flags=["read"], succeed=True)
    bar = next(proxies).create_client("bar")
    bar.open(du.uri_fanout_bar, flags=["read"], succeed=True)
    baz = next(proxies).create_client("baz")
    baz.open(du.uri_fanout_baz, flags=["read"], succeed=True)

    # In a moment we'll make sure no messages are sent to 'foo' after it
    # has been unregistered, so we need to eat the push event for the
    # message posted while 'foo' was still valid.
    foo.wait_push_event()

    set_app_ids(cluster, [a for a in default_app_ids if a not in ["foo"]], du)

    @attempt(3)
    def _():
        leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
        assert leader.outputs_substr("Num virtual storages: 2")
        assert leader.outputs_substr("foo: status=unauthorized")

    test_logger.info("confirm msg 1 for bar, expecting 1 msg in storage")
    time.sleep(1)  # Let the message reach the proxy
    bar.confirm(du.uri_fanout_bar, "+1", succeed=True)

    @attempt(3)
    def _():
        leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
        assert leader.outputs_regex("Storage.*: 1 messages")

    test_logger.info("confirm msg 1 for baz, expecting 0 msg in storage")
    time.sleep(1)  # Let the message reach the proxy
    baz.confirm(du.uri_fanout_baz, "+1", succeed=True)

    @attempt(3)
    def _():
        leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
        assert leader.outputs_regex("Storage.*: 0 messages")

    producer.post(du.uri_fanout, ["after-unregister"], block=True)

    assert bar.wait_push_event()
    assert len(bar.list(du.uri_fanout_bar, block=True)) == 1
    assert baz.wait_push_event()
    assert len(baz.list(du.uri_fanout_baz, block=True)) == 1

    assert not foo.wait_push_event(timeout=1)
    foo_msgs = foo.list(du.uri_fanout_foo, block=True)
    assert len(foo_msgs) == 1
    assert foo_msgs[0].payload == "before-unregister"

    assert Client.e_SUCCESS == foo.confirm(
        du.uri_fanout_foo, foo_msgs[0].guid, block=True
    )
    assert Client.e_SUCCESS == foo.close(du.uri_fanout_foo, block=True)

    # Re-authorize
    set_app_ids(cluster, default_app_ids, du)

    foo.open(du.uri_fanout_foo, flags=["read"], succeed=True)
    producer.post(du.uri_fanout, ["after-reauthorize"], block=True)

    @attempt(3)
    def _():
        leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
        leader.outputs_regex(r"foo.*: status=alive")

    assert foo.wait_push_event()
    foo_msgs = foo.list(du.uri_fanout_foo, block=True)
    assert len(foo_msgs) == 1
    assert foo_msgs[0].payload == "after-reauthorize"


def test_dynamic_twice_alarm_once(cluster: Cluster, domain_urls: tc.DomainUrls):
    uri_fanout = domain_urls.uri_fanout
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(uri_fanout, flags=["write,ack"], succeed=True)

    # ---------------------------------------------------------------------
    # Create a consumer for the unauthorized substream. This should succeed
    # but with an ALARM.

    consumer1 = next(proxies).create_client("consumer1")
    assert (
        consumer1.open(f"{uri_fanout}?id=quux", flags=["read"], block=True)
        == Client.e_SUCCESS
    )
    assert leader.alarms()

    # ---------------------------------------------------------------------
    # Create a consumer for the same unauthorized substream. This should
    # succeed and no ALARM should be generated.

    leader.drain()
    consumer2 = next(proxies).create_client("consumer2")
    assert (
        consumer2.open(f"{uri_fanout}?id=quux", flags=["read"], block=True)
        == Client.e_SUCCESS
    )
    assert not leader.alarms()

    # ---------------------------------------------------------------------
    # Close both unauthorized substreams and re-open new one.  It should
    # succeed and alarm again.

    consumer1.close(f"{uri_fanout}?id=quux", succeed=True)
    consumer2.close(f"{uri_fanout}?id=quux", succeed=True)

    assert (
        consumer2.open(f"{uri_fanout}?id=quux", flags=["read"], block=True)
        == Client.e_SUCCESS
    )
    assert leader.alarms()


@set_max_messages
def test_unauthorized_appid_doesnt_hold_messages(
    cluster: Cluster, domain_urls: tc.DomainUrls
):
    # Goal: check that dynamically allocated, but not yet authorized,
    # substreams do not hold messages in fanout queues.
    uri_fanout = domain_urls.uri_fanout
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(uri_fanout, flags=["write,ack"], succeed=True)

    # ---------------------------------------------------------------------
    # fill queue to capacity

    for i in range(max_msgs):
        producer.post(uri_fanout, [f"msg{i}"], block=True)
        if producer.outputs_regex("ERROR.*Failed ACK.*LIMIT_MESSAGES", timeout=0):
            break

    # ---------------------------------------------------------------------
    # dynamically create a substream
    unauthorized_consumer = next(proxies).create_client("unauthorized_consumer")
    unauthorized_consumer.open(f"{uri_fanout}?id=unauthorized", flags=["read"])
    assert leader.alarms()

    # ---------------------------------------------------------------------
    # consume all the messages in all the authorized substreams

    # pylint: disable=cell-var-from-loop; passing lambda to 'wait_until' is safe
    for app_id in default_app_ids:
        appid_uri = f"{uri_fanout}?id={app_id}"
        consumer = next(proxies).create_client(app_id)
        consumer.open(appid_uri, flags=["read"], succeed=True)
        assert consumer.wait_push_event()
        assert wait_until(
            lambda: len(consumer.list(appid_uri, block=True)) == max_msgs, 3
        )
        consumer.confirm(appid_uri, "*", succeed=True)

    # ---------------------------------------------------------------------
    # process a new message to confirm that 'unauthorized' substream did
    # not hold messages
    producer.post(uri_fanout, ["newMsg"], block=True)
    assert consumer.wait_push_event()
    msgs = consumer.list(appid_uri, block=True)
    assert len(msgs) == 1


@set_max_messages
def test_deauthorized_appid_doesnt_hold_messages(
    cluster: Cluster, domain_urls: tc.DomainUrls
):
    # Goal: check that dynamically de-authorized substreams do not hold
    # messages in fanout queues.
    uri_fanout = domain_urls.uri_fanout
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    # ---------------------------------------------------------------------
    # force the leader to load the domain so we can unregister the appids
    producer = next(proxies).create_client("producer")
    producer.open(uri_fanout, flags=["write,ack"], succeed=True)

    # ---------------------------------------------------------------------
    # and remove all the queues otherwise unregistration will fail
    producer.close(uri_fanout, succeed=True)
    leader.force_gc_queues(succeed=True)

    # ---------------------------------------------------------------------
    # unauthorize 'bar' and 'baz'
    set_app_ids(
        cluster, [a for a in default_app_ids if a not in ["bar", "baz"]], domain_urls
    )

    # ---------------------------------------------------------------------
    # fill queue to capacity
    time.sleep(1)
    producer.open(uri_fanout, flags=["write,ack"], succeed=True)
    num_msgs = 4

    for i in range(0, num_msgs):
        producer.post(uri_fanout, [f"msg{i}"], succeed=True)

    # ---------------------------------------------------------------------
    # consume messages in the 'foo' substream
    appid_uri = f"{uri_fanout}?id=foo"
    consumer = next(proxies).create_client("foo")
    consumer.open(appid_uri, flags=["read"], succeed=True)
    assert consumer.wait_push_event()
    assert wait_until(lambda: len(consumer.list(appid_uri, block=True)) == num_msgs, 3)
    msgs = consumer.list(appid_uri, block=True)
    for _ in msgs:
        consumer.confirm(appid_uri, "+1", succeed=True)

    # process a new message to confirm that 'bar' and 'baz' substreams did
    # not hold messages
    producer.post(uri_fanout, ["newMsg"], block=True)
    assert consumer.wait_push_event()
    msgs = consumer.list(appid_uri, block=True)
    assert len(msgs) == 1


def test_unauthorization(cluster: Cluster, domain_urls: tc.DomainUrls):
    # Goal: check that dynamically unauthorizing apps with live consumers
    #       invalidates their virtual iterators
    uri_fanout = domain_urls.uri_fanout
    proxies = cluster.proxy_cycle()

    # ---------------------------------------------------------------------
    # get producer and "foo" consumer
    producer = next(proxies).create_client("producer")
    producer.open(uri_fanout, flags=["write,ack"], succeed=True)

    appid_uri = f"{uri_fanout}?id=foo"
    consumer = next(proxies).create_client("foo")
    consumer.open(appid_uri, flags=["read"], succeed=True)

    producer.post(uri_fanout, ["msg1"], succeed=True)

    # ---------------------------------------------------------------------
    # unauthorize everything
    set_app_ids(cluster, [], domain_urls)

    # ---------------------------------------------------------------------
    # if iterators are not invalidated, 'afterNewMessage' will crash
    producer.post(uri_fanout, ["msg2"], succeed=True)

    # ---------------------------------------------------------------------
    # check if the leader is still there
    appid_uri = f"{uri_fanout}?id=bar"
    consumer = next(proxies).create_client("bar")
    consumer.open(appid_uri, flags=["read"], succeed=True)


def test_two_consumers_of_unauthorized_app(
    multi_node: Cluster, domain_urls: tc.DomainUrls
):
    """Ticket 167201621: First client open authorized and unauthorized apps;
    second client opens unauthorized app.
    Then, primary shuts down causing replica to issue wildcard close
    requests to primary.
    """

    du = domain_urls
    leader = multi_node.last_known_leader

    replica1 = multi_node.nodes()[0]
    if replica1 == leader:
        replica1 = multi_node.nodes()[1]

    # ---------------------------------------------------------------------
    # Two "foo" and "unauthorized" consumers
    consumer1 = replica1.create_client("consumer1")
    consumer1.open(du.uri_fanout_foo, flags=["read"], succeed=True)
    consumer1.open(f"{du.uri_fanout}?id=unauthorized", flags=["read"], succeed=True)

    replica2 = multi_node.nodes()[2]
    if replica2 == leader:
        replica2 = multi_node.nodes()[3]

    consumer2 = replica2.create_client("consumer2")
    consumer2.open(f"{du.uri_fanout}?id=unauthorized", flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # shutdown and wait

    leader.stop()


@tweak.cluster.cluster_attributes.is_cslmode_enabled(False)
@tweak.cluster.cluster_attributes.is_fsmworkflow(False)
def test_open_authorize_restart_from_non_FSM_to_FSM(
    cluster: Cluster, domain_urls: tc.DomainUrls
):
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    all_app_ids = default_app_ids + ["quux"]

    # ---------------------------------------------------------------------
    # Create a consumer for each authorized substream.

    consumers = {}

    for app_id in all_app_ids:
        consumer = next(proxies).create_client(app_id)
        consumers[app_id] = consumer
        consumer.open(f"{du.uri_fanout}?id={app_id}", flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(cluster, default_app_ids + ["quux"], du)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(du.uri_fanout, ["msg1"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # Post a second message.

    producer.post(du.uri_fanout, ["msg2"])
    assert producer.outputs_regex(r"MESSAGE.*ACK", timeout)

    # ---------------------------------------------------------------------
    # Ensure that all substreams get 2 messages

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)
    # pylint: disable=cell-var-from-loop; passing lambda to 'wait_until' is safe
    for app_id in all_app_ids:
        test_logger.info(f"Check if {app_id} has seen 2 messages")
        assert wait_until(
            lambda: len(
                consumers[app_id].list(f"{du.uri_fanout}?id={app_id}", block=True)
            )
            == 2,
            3,
        )

    # Save one confirm to the storage for 'quux' only
    consumers["quux"].confirm(f"{du.uri_fanout}?id=quux", "+1", succeed=True)

    for app_id in all_app_ids:
        assert (
            consumers[app_id].close(f"{du.uri_fanout}?id={app_id}", block=True)
            == Client.e_SUCCESS
        )

    cluster.stop_nodes()

    # Reconfigure the cluster from non-FSM to FSM mode
    for broker in cluster.configurator.brokers.values():
        my_clusters = broker.clusters.my_clusters
        if len(my_clusters) > 0:
            my_clusters[0].cluster_attributes.is_cslmode_enabled = True
            my_clusters[0].cluster_attributes.is_fsmworkflow = True
    cluster.deploy_domains()

    cluster.start_nodes(wait_leader=True, wait_ready=True)
    # For a standard cluster, states have already been restored as part of
    # leader re-election.
    if cluster.is_single_node:
        producer.wait_state_restored()

    for app_id in all_app_ids:
        consumer = next(proxies).create_client(app_id)
        consumers[app_id] = consumer
        consumer.open(f"{du.uri_fanout}?id={app_id}", flags=["read"], succeed=True)

    # pylint: disable=cell-var-from-loop; passing lambda to 'wait_until' is safe
    for app_id in default_app_ids:
        test_logger.info(f"Check if {app_id} has seen 2 messages")
        assert wait_until(
            lambda: len(
                consumers[app_id].list(f"{du.uri_fanout}?id={app_id}", block=True)
            )
            == 2,
            3,
        )

    assert wait_until(
        lambda: len(consumers["quux"].list(f"{du.uri_fanout}?id=quux", block=True))
        == 1,
        3,
    )

    for app_id in all_app_ids:
        assert (
            consumers[app_id].close(f"{du.uri_fanout}?id={app_id}", block=True)
            == Client.e_SUCCESS
        )


def test_csl_repair_after_stop(
    cluster: Cluster,
    domain_urls: tc.DomainUrls,  # pylint: disable=unused-argument
):
    """Adding Apps to an existing queue in the absense of primary results in
    the App missing in the CSL.  The CSL needs repair
    """
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(tc.URI_FANOUT_SC, flags=["write,ack"], succeed=True)

    producer.post(tc.URI_FANOUT_SC, ["msg1"], block=True)

    producer.close(tc.URI_FANOUT_SC, succeed=True)

    cluster.stop_nodes()

    updated_app_ids = default_app_ids.copy()
    updated_app_ids.remove("foo")
    updated_app_ids.append("new1")

    cluster.config.domains[
        tc.DOMAIN_FANOUT_SC
    ].definition.parameters.mode.fanout.app_ids = updated_app_ids

    cluster.deploy_domains()

    cluster.start_nodes(wait_leader=True, wait_ready=True)

    producer.open(tc.URI_FANOUT_SC, flags=["write,ack"], succeed=True)


def test_open_authorize_change_primary(multi_node: Cluster, domain_urls: tc.DomainUrls):
    """Add an App to Domain config of an existing queue, and then force a
    Replica to become new Primary.  Start new Consumer.  Make sure the Consumer
    receives previously posted data.
    This is to address the concern with Replica not processing QueueUpdates
    before becoming Primary.
    """
    du = domain_urls
    leader = multi_node.last_known_leader
    proxies = multi_node.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    all_app_ids = default_app_ids + ["new_app"]

    # ---------------------------------------------------------------------
    # Create a consumer
    app_id = all_app_ids[0]
    consumer = next(proxies).create_client(app_id)
    consumer.open(f"{du.uri_fanout}?id={app_id}", flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # Authorize 'quux'.
    set_app_ids(multi_node, all_app_ids, du)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(du.uri_fanout, ["msg1"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # Ensure that all substreams get 2 messages

    leader.dump_queue_internals(du.domain_fanout, tc.TEST_QUEUE)

    assert wait_until(
        lambda: len(consumer.list(f"{du.uri_fanout}?id={app_id}", block=True)) == 1,
        3,
    )

    consumer.close(f"{du.uri_fanout}?id={app_id}", block=True, succeed=True)

    leader.check_exit_code = False
    leader.kill()
    leader.wait()

    # wait for new leader
    leader = multi_node.wait_leader()

    consumer = next(proxies).create_client(app_id)
    consumer.open(f"{du.uri_fanout}?id=new_app", flags=["read"], succeed=True)

    assert wait_until(
        lambda: len(consumer.list(f"{du.uri_fanout}?id=new_app", block=True)) == 1,
        3,
    )

    consumer.close(f"{du.uri_fanout}?id=new_app", block=True, succeed=True)


def test_old_data_new_app(
    cluster: Cluster,
    domain_urls: tc.DomainUrls,  # pylint: disable=unused-argument
):
    """Do this: m1, +new_app_1, m2, +new_app2, m3, +new_app3, m4, -new_app2
    Old apps  receive  4
    new_app_1 receives 3
    new_app_2 receives 0 (after it gets deleted)
    new_app_3 receives 1

    Restart and receive the same (except for new_app_2)

    Confirm everything and verify empty storage
    """

    def _set_app_ids(cluster: Cluster, app_ids: List[str]):
        cluster.config.domains[
            tc.DOMAIN_FANOUT_SC
        ].definition.parameters.mode.fanout.app_ids = app_ids  # type: ignore
        cluster.reconfigure_domain(tc.DOMAIN_FANOUT_SC, succeed=True)

    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    producer = next(proxies).create_client("producer")
    producer.open(tc.URI_FANOUT_SC, flags=["write,ack"], succeed=True)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(tc.URI_FANOUT_SC, ["m1"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # +new_app_1
    new_app_1 = "new_app_1"
    _set_app_ids(cluster, default_app_ids + [new_app_1])

    leader.capture(f"Registered appId '{new_app_1}'", timeout=5)

    # ---------------------------------------------------------------------
    # Post
    producer.post(tc.URI_FANOUT_SC, ["m2"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # +new_app_2
    new_app_2 = "new_app_2"
    _set_app_ids(cluster, default_app_ids + [new_app_1] + [new_app_2])

    leader.capture(f"Registered appId '{new_app_2}'", timeout=5)

    # ---------------------------------------------------------------------
    # Post
    producer.post(tc.URI_FANOUT_SC, ["m3"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # +new_app_3
    new_app_3 = "new_app_3"
    _set_app_ids(cluster, default_app_ids + [new_app_1] + [new_app_2] + [new_app_3])

    leader.capture(f"Registered appId '{new_app_3}'", timeout=5)

    # ---------------------------------------------------------------------
    # Post
    producer.post(tc.URI_FANOUT_SC, ["m4"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # -new_app_2
    _set_app_ids(cluster, default_app_ids + [new_app_1] + [new_app_3])

    # ---------------------------------------------------------------------
    # Post extra strong consistency message after removing new_app_2 but before
    # cluster.stop_nodes to serve as a synchronization.
    producer.post(tc.URI_FANOUT_SC, ["m5"], succeed=True, wait_ack=True)

    leader.capture(f"Uregistered appId '{new_app_1}'", timeout=5)

    consumers = {}

    def _verify_delivery(consumer, uri, count, andConfirm=False, timeout=2):
        if count:
            consumer.wait_push_event()

        assert wait_until(lambda: len(consumer.list(uri, block=True)) == count, timeout)
        if count and andConfirm:
            assert consumer.confirm(uri, f"+{count}", block=True) == Client.e_SUCCESS

    def _verify_clients(andConfirm=False):
        # -----------------------------------------------------------------
        # Ensure that all _default_ substreams get 4 messages

        # -----------------------------------------------------------------
        # Create a consumer for each default substream.

        for app_id in default_app_ids:
            consumer = next(proxies).create_client(app_id)
            consumers[app_id] = consumer
            consumer.open(
                f"{tc.URI_FANOUT_SC}?id={app_id}", flags=["read"], succeed=True
            )

        # Once queue is created
        leader = cluster.last_known_leader
        leader.list_messages(tc.DOMAIN_FANOUT_SC, tc.TEST_QUEUE, 0, 100)
        assert leader.outputs_substr(f"Printing 5 message(s)", 5)

        new_consumer_1 = next(proxies).create_client(new_app_1)
        new_consumer_1.open(
            f"{tc.URI_FANOUT_SC}?id={new_app_1}", flags=["read"], succeed=True
        )

        new_consumer_2 = next(proxies).create_client(new_app_2)
        new_consumer_2.open(
            f"{tc.URI_FANOUT_SC}?id={new_app_2}", flags=["read"], succeed=True
        )

        new_consumer_3 = next(proxies).create_client(new_app_3)
        new_consumer_3.open(
            f"{tc.URI_FANOUT_SC}?id={new_app_3}", flags=["read"], succeed=True
        )

        leader.dump_queue_internals(tc.DOMAIN_FANOUT_SC, tc.TEST_QUEUE)

        for app_id in default_app_ids:
            test_logger.info(f"Check if {app_id} has seen 5 messages")
            _verify_delivery(
                consumers[app_id], f"{tc.URI_FANOUT_SC}?id={app_id}", 5, andConfirm
            )

        test_logger.info(f"Check if {new_app_1} has seen 4 messages")
        _verify_delivery(
            new_consumer_1, f"{tc.URI_FANOUT_SC}?id={new_app_1}", 4, andConfirm
        )

        test_logger.info(f"Check if {new_app_2} has seen 0 messages")
        _verify_delivery(new_consumer_2, f"{tc.URI_FANOUT_SC}?id={new_app_2}", 0, False)

        test_logger.info(f"Check if {new_app_3} has seen 2 message")
        _verify_delivery(
            new_consumer_3, f"{tc.URI_FANOUT_SC}?id={new_app_3}", 2, andConfirm
        )

        # ---------------------------------------------------------------------
        # Stop all clients

        for app_id in default_app_ids:
            consumers[app_id].close(
                f"{tc.URI_FANOUT_SC}?id={app_id}", block=True, succeed=True
            )

        new_consumer_1.close(
            f"{tc.URI_FANOUT_SC}?id={new_app_1}", block=True, succeed=True
        )
        new_consumer_2.close(
            f"{tc.URI_FANOUT_SC}?id={new_app_2}", block=True, succeed=True
        )
        new_consumer_3.close(
            f"{tc.URI_FANOUT_SC}?id={new_app_3}", block=True, succeed=True
        )

    _verify_clients()

    cluster.stop_nodes()

    cluster.start_nodes(wait_leader=True, wait_ready=True)

    leader = cluster.last_known_leader

    _verify_clients(andConfirm=True)

    leader.list_messages(tc.DOMAIN_FANOUT_SC, tc.TEST_QUEUE, 0, 100)
    assert leader.outputs_substr(f"Printing 0 message(s)", 5)


def test_proxy_partial_push(
    cluster: Cluster,
    domain_urls: tc.DomainUrls,  # pylint: disable=unused-argument
):
    """Make Proxy receive PUSH after closing one App"""

    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    proxy = next(proxies)
    producer = proxy.create_client("producer")
    producer.open(tc.URI_FANOUT, flags=["write,ack"], succeed=True)

    consumers = {}

    for app_id in default_app_ids:
        consumer = proxy.create_client(app_id)
        consumers[app_id] = consumer
        consumer.open(f"{tc.URI_FANOUT}?id={app_id}", flags=["read"], succeed=True)

    consumers["bar"].close(f"{tc.URI_FANOUT}?id=bar", block=True, succeed=True)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(tc.URI_FANOUT, ["m1"], succeed=True, wait_ack=True)

    # consumers["bar"].wait_push_event()
    #
    # producer.post(tc.URI_FANOUT, ["m2"], succeed=True, wait_ack=True)

    consumers["foo"].wait_push_event()

    consumers["foo"].close(f"{tc.URI_FANOUT}?id=foo", block=True, succeed=True)
    consumers["baz"].close(f"{tc.URI_FANOUT}?id=baz", block=True, succeed=True)


@tweak.domain.message_ttl(3)
def test_gc_old_data_new_app(cluster: Cluster, domain_urls: tc.DomainUrls):
    """Trigger old message GC in the presence of new App.  Need to allocate
    Apps states first.
    """
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()
    proxy = next(proxies)

    producer = proxy.create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    app_id = default_app_ids[0]
    consumer = proxy.create_client(app_id)
    consumer_uri = f"{du.uri_fanout}?id={app_id}"
    consumer.open(consumer_uri, flags=["read"], succeed=True)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(du.uri_fanout, ["m1"], succeed=True, wait_ack=True)

    # confirm, to make sure App state is allocated
    consumer.wait_push_event()

    assert wait_until(
        lambda: len(consumer.list(consumer_uri, block=True)) == 1, timeout
    )
    assert consumer.confirm(consumer_uri, f"+{1}", block=True) == Client.e_SUCCESS

    # ---------------------------------------------------------------------
    # +new_app_1
    new_app_1 = "new_app_1"
    set_app_ids(cluster, default_app_ids + [new_app_1], du)

    assert consumer.close(consumer_uri, block=True) == Client.e_SUCCESS

    # Observe that the message was GC'd from the queue.
    leader.capture(
        f"queue \\[{du.uri_fanout}\\].*garbage-collected \\[1\\] messages", timeout=5
    )

    leader.list_messages(du.domain_fanout, tc.TEST_QUEUE, 0, 100)
    assert leader.outputs_substr(f"Printing 0 message(s)", 5)

    leader.list_messages(du.domain_fanout, tc.TEST_QUEUE, 0, 100, appid=app_id)
    assert leader.outputs_substr(f"Printing 0 message(s)", 5)

    leader.list_messages(du.domain_fanout, tc.TEST_QUEUE, 0, 100, appid=new_app_1)
    assert leader.outputs_substr(f"Printing 0 message(s)", 5)


def test_add_remove_add_app(cluster: Cluster, domain_urls: tc.DomainUrls):
    """Test adding, removing, and adding the same App."""
    du = domain_urls
    leader = cluster.last_known_leader
    proxies = cluster.proxy_cycle()

    # pick proxy in datacenter opposite to the primary's
    next(proxies)

    proxy = next(proxies)

    producer = proxy.create_client("producer")
    producer.open(du.uri_fanout, flags=["write,ack"], succeed=True)

    # ---------------------------------------------------------------------
    # +new_app_1
    new_app_1 = "new_app_1"
    set_app_ids(cluster, default_app_ids + [new_app_1], du)

    leader.capture(f"Registered appId '{new_app_1}'", timeout=5)

    # ---------------------------------------------------------------------
    # Post a message.
    producer.post(du.uri_fanout, ["msg1"], succeed=True, wait_ack=True)

    # ---------------------------------------------------------------------
    # consume the messages in all the authorized substreams

    # pylint: disable=cell-var-from-loop; passing lambda to 'wait_until' is safe
    for app_id in default_app_ids:
        appid_uri = f"{du.uri_fanout}?id={app_id}"
        consumer = next(proxies).create_client(app_id)
        consumer.open(appid_uri, flags=["read"], succeed=True)
        assert consumer.wait_push_event()
        assert wait_until(lambda: len(consumer.list(appid_uri, block=True)) == 1, 3)
        consumer.confirm(appid_uri, "*", succeed=True)

    # ---------------------------------------------------------------------
    # -new_app_1
    set_app_ids(cluster, default_app_ids, du)

    leader.capture(f"Unregistered appId '{new_app_1}'", timeout=5)

    # ---------------------------------------------------------------------
    # +new_app_1
    new_app_1 = "new_app_1"
    set_app_ids(cluster, default_app_ids + [new_app_1], du)

    leader.capture(f"Registered appId '{new_app_1}'", timeout=5)

    cluster.stop_nodes()

    cluster.start_nodes(wait_leader=True, wait_ready=True)

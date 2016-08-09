# -*- coding: utf-8 -*-

# Copyright 2014 Spanish National Research Council (CSIC)
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import ceilometerclient.client
import dateutil.parser
from oslo_config import cfg
from oslo_log import log

from caso.extract import nova
from caso import keystone_client

CONF = cfg.CONF

LOG = log.getLogger(__name__)


class CeilometerExtractor(nova.OpenStackExtractor):
    def __init__(self):
        super(CeilometerExtractor, self).__init__()

    def _get_ceilometer_client(self, tenant):
        session = keystone_client.get_session(CONF, tenant)
        return ceilometerclient.client.get_client('2', session=session)

    def _build_query(self, project_id=None, start_date=None, end_date=None):
        q = []
        if project_id:
            q.append({'field': 'project_id', 'value': project_id})
        if start_date:
            q.append({'field': 'timestamp', 'op': 'ge', 'value': start_date})
        if end_date:
            q.append({'field': 'timestamp', 'op': 'le', 'value': end_date})
        return q

    def _fill_metric(self, metric_name, samples, records,
                     get_id=lambda s: s.resource_id,
                     unit_conv=lambda v: v):
        """Fills a given metric in the records

        get_id is a function that gets the instance id from the sample
        conv is a function that converts the metric to the units
             requested by the usage record

        """

        instance_timestamps = {}
        for sample in samples:
            instance_id = get_id(sample)
            try:
                r = records[instance_id]
            except KeyError:
                continue
            # takes the maximum value from ceilometer
            sample_value = unit_conv(sample.counter_volume)
            instance_ts = instance_timestamps.get(instance_id, None)
            if instance_ts is None:
                r.__dict__[metric_name] = sample_value
            try:
                r.__dict__[metric_name] = max(r.__dict__[metric_name],
                                              sample_value)
            except KeyError:
                r.__dict__[metric_name] = sample_value
            sample_ts = dateutil.parser.parse(sample.timestamp)
            instance_timestamps[instance_id] = sample_ts

    def _fill_cpu_metric(self, cpu_samples, records):
        self._fill_metric('cpu_duration', cpu_samples, records,
                          # convert ns to s
                          unit_conv=lambda v: int(v / 1e9))

    def _fill_net_metric(self, metric_name, net_samples, records):
        self._fill_metric(metric_name, net_samples, records,
                          lambda s: s.resource_metadata.get('instance_id'),
                          # convert bytes to GB
                          unit_conv=lambda v: int(v / 2 ** 30))

    def extract_for_tenant(self, tenant, lastrun, extract_to):
        """Extract records for a tenant from given date.

        This method will get information from nova, and will enhance it with
        information from ceilometer.

        :param tenant: Tenant to extract records for.
        :param extract_from: datetime.datetime object indicating the date to
                             extract records from
        :param extract_to: datetime.datetime object indicating the date to
                             extract records to
        :returns: A dictionary of {"server_id": caso.record.Record"}
        """
        records = super(CeilometerExtractor,
                        self).extract_for_tenant(tenant, lastrun, extract_to)
        # Try and except here
        ks_conn = self._get_keystone_client(tenant)
        conn = self._get_ceilometer_client(tenant)
        # See comment in nova.py, remove TZ from the dates.
        lastrun = lastrun.replace(tzinfo=None)
        search_query = self._build_query(ks_conn.tenant_id, lastrun,
                                         extract_to)

        cpu = conn.samples.list(meter_name='cpu', q=search_query)
        self._fill_cpu_metric(cpu, records)
        net_in = conn.samples.list(meter_name='network.incoming.bytes',
                                   q=search_query)
        self._fill_net_metric('network_in', net_in, records)
        net_out = conn.samples.list(meter_name='network.outcoming.bytes',
                                    q=search_query)
        self._fill_net_metric('network_out', net_out, records)

        return records

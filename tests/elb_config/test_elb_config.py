#                           PUBLIC DOMAIN NOTICE
#              National Center for Biotechnology Information
#  
# This software is a "United States Government Work" under the
# terms of the United States Copyright Act.  It was written as part of
# the authors' official duties as United States Government employees and
# thus cannot be copyrighted.  This software is freely available
# to the public for use.  The National Library of Medicine and the U.S.
# Government have not placed any restriction on its use or reproduction.
#   
# Although all reasonable efforts have been taken to ensure the accuracy
# and reliability of the software and data, the NLM and the U.S.
# Government do not and cannot warrant the performance or results that
# may be obtained by using this software or data.  The NLM and the U.S.
# Government disclaim all warranties, express or implied, including
# warranties of performance, merchantability or fitness for any particular
# purpose.
#   
# Please cite NCBI in any work or product based on this material.

"""
Tests for elb/elb_config.py

Author: Greg Boratyn (boratyn@ncbi.nlm.nih.gov)
Created: Fri 26 Feb 2021 05:28:21 AM EDT
"""

import re
import configparser
from dataclasses import dataclass, fields
from unittest.mock import MagicMock, patch
import math
from elastic_blast.constants import CSP
from elastic_blast.constants import CFG_CLOUD_PROVIDER
from elastic_blast.constants import CFG_CP_GCP_PROJECT, CFG_CP_GCP_REGION, CFG_CP_GCP_ZONE
from elastic_blast.constants import CFG_CP_GCP_NETWORK, CFG_CP_GCP_SUBNETWORK
from elastic_blast.constants import CFG_CP_AWS_REGION, CFG_CP_AWS_VPC, CFG_CP_AWS_SUBNET
from elastic_blast.constants import CFG_CP_AWS_JOB_ROLE, CFG_CP_AWS_BATCH_SERVICE_ROLE
from elastic_blast.constants import CFG_CP_AWS_INSTANCE_ROLE, CFG_CP_AWS_SPOT_FLEET_ROLE
from elastic_blast.constants import CFG_CP_AWS_SECURITY_GROUP, CFG_CP_AWS_KEY_PAIR
from elastic_blast.constants import CFG_BLAST, CFG_BLAST_PROGRAM, CFG_BLAST_DB
from elastic_blast.constants import CFG_BLAST_DB_SRC, CFG_BLAST_RESULTS, CFG_BLAST_QUERY
from elastic_blast.constants import CFG_BLAST_OPTIONS, CFG_BLAST_BATCH_LEN
from elastic_blast.constants import CFG_BLAST_MEM_REQUEST, CFG_BLAST_MEM_LIMIT
from elastic_blast.constants import CFG_BLAST_TAXIDLIST, CFG_BLAST_DB_MEM_MARGIN
from elastic_blast.constants import ELB_BLASTDB_MEMORY_MARGIN
from elastic_blast.constants import CFG_CLUSTER, CFG_CLUSTER_NAME, CFG_CLUSTER_MACHINE_TYPE
from elastic_blast.constants import CFG_CLUSTER_NUM_NODES, CFG_CLUSTER_NUM_CPUS
from elastic_blast.constants import CFG_CLUSTER_PD_SIZE, CFG_CLUSTER_USE_PREEMPTIBLE
from elastic_blast.constants import CFG_CLUSTER_DRY_RUN, CFG_CLUSTER_DISK_TYPE
from elastic_blast.constants import CFG_CLUSTER_PROVISIONED_IOPS, CFG_CLUSTER_BID_PERCENTAGE
from elastic_blast.constants import CFG_CLUSTER_LABELS, CFG_CLUSTER_EXP_USE_LOCAL_SSD
from elastic_blast.constants import CFG_CLUSTER_ENABLE_STACKDRIVER
from elastic_blast.constants import ELB_DFLT_NUM_NODES
from elastic_blast.constants import ELB_DFLT_USE_PREEMPTIBLE
from elastic_blast.constants import ELB_DFLT_GCP_PD_SIZE, ELB_DFLT_AWS_PD_SIZE
from elastic_blast.constants import ELB_DFLT_GCP_MACHINE_TYPE, ELB_DFLT_AWS_MACHINE_TYPE
from elastic_blast.constants import ELB_DFLT_INIT_PV_TIMEOUT, ELB_DFLT_BLAST_K8S_TIMEOUT
from elastic_blast.constants import ELB_DFLT_AWS_SPOT_BID_PERCENTAGE
from elastic_blast.constants import ELB_DFLT_AWS_DISK_TYPE, ELB_DFLT_OUTFMT
from elastic_blast.constants import ELB_DFLT_AWS_NUM_CPUS, ELB_DFLT_GCP_NUM_CPUS
from elastic_blast.constants import INPUT_ERROR, ELB_DFLT_AWS_REGION, BLASTDB_ERROR
from elastic_blast.constants import SYSTEM_MEMORY_RESERVE, MolType
from elastic_blast.base import ConfigParserToDataclassMapper, ParamInfo, DBSource
from elastic_blast.base import InstanceProperties
from elastic_blast.elb_config import CloudURI, GCPString, AWSRegion
from elastic_blast.elb_config import GCPConfig, AWSConfig, BlastConfig, ClusterConfig
from elastic_blast.elb_config import ElasticBlastConfig, get_instance_props
from elastic_blast.constants import ElbCommand
from elastic_blast.util import UserReportError, get_query_batch_size, ElbSupportedPrograms
from elastic_blast.gcp_traits import get_machine_properties as gcp_get_machine_properties
from elastic_blast.aws_traits import get_machine_properties as aws_get_machine_properties
from tests.utils import gke_mock, DB_METADATA_PROT as MOCK_DB_METADATA

import pytest


def test_default_labels(gke_mock):
    DB = 'My:Fancy*DB65'
    gke_mock.cloud.storage[f'gs://blast-db/000/{DB}-nucl-metadata.json'] = MOCK_DB_METADATA

    cfg = ElasticBlastConfig(gcp_project = 'test-gcp-project',
                             gcp_region = 'test-gcp-region',
                             gcp_zone = 'test-gcp-zone',
                             program = 'blastn',
                             db = DB,
                             queries = 'test-queries.fa',
                             results = 'gs://some-bucket-with-interesting-name',
                             cluster_name = 'some-cluster-name',
                             task = ElbCommand.SUBMIT)

    labels = cfg.cluster.labels
    # "Label keys must start with a lowercase letter."
    # From https://cloud.google.com/compute/docs/labeling-resources#label_format
    assert(not re.search(r'[A-Z]', labels))

    # Parse generated labels and verify some parts
    parts = labels.split(',')
    label_dict = {key: value for key, value in map(lambda x: x.split('='), parts)}
    assert(label_dict['project'] == 'elastic-blast')
    assert(label_dict['cluster-name'] == 'some-cluster-name')
    assert('client-hostname' in label_dict)
    assert('created' in label_dict)
    created_date = label_dict['created']
    assert(re.match(r'[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}', created_date))
    assert(label_dict['owner'] == label_dict['creator'])
    assert(label_dict['db'] == 'my-fancy-db65')
    assert(label_dict['program'] == 'blastn')
    assert(label_dict['billingcode'] == 'elastic-blast')
    assert(label_dict['results'] == 'gs---some-bucket-with-interesting-name')
    print('labels', labels)


def test_clouduri():
    """Test CloudURI type"""
    assert issubclass(CloudURI, str)
    for val in ['gs://bucket-123', 's3://bucket-123']:
        assert CloudURI(val) == val

    for val in [123, 'bucket', 'gs:bucket', 's3//bucket', 's3://bucket!@#$']:
        with pytest.raises(ValueError):
            CloudURI(val)

    uri = CloudURI('s3://bucket')
    assert len(uri.md5) 


def test_gcpstring():
    """Test GCPString type"""
    assert issubclass(GCPString, str)
    for val in ['us-east-4b', 'some-string', 'name-1234']:
        assert GCPString(val) == val

    for val in ['UPPERCASE', 'some@name', '']:
        with pytest.raises(ValueError):
            GCPString(val)
            

def test_awsregion():
    """Test AWSRegion type"""
    assert issubclass(AWSRegion, str)
    for val in ['us-east-1', 'some-Region', 'REGION-123']:
        assert AWSRegion(val) == val

    for val in ['re@ion', 'region-!@#', '']:
        with pytest.raises(ValueError):
            AWSRegion(val)


def test_gcpconfig():
    """Test GCPConfig defaults"""
    PROJECT = 'test-project'
    REGION = 'test-region'
    ZONE = 'test-zone'

    cfg = GCPConfig(project = PROJECT,
                    region = REGION,
                    zone = ZONE)

    assert cfg.cloud == CSP.GCP
    assert cfg.project == PROJECT
    assert cfg.region == REGION
    assert cfg.zone == ZONE
    assert not cfg.network
    assert not cfg.subnet
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


def test_gcpconfig_validation():
    """Test GCPConfig validation"""
    cfg = GCPConfig(project = 'test-project',
                    region = 'test-region',
                    zone = 'test-zone')

    cfg.network = 'some-network'
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert errors
    assert [message for message in errors if 'gcp-network and gcp-subnetwork' in message]

    cfg.network = None
    cfg.subnet = 'subnet'
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert errors
    assert [message for message in errors if 'gcp-network and gcp-subnetwork' in message]


def test_gcpconfig_from_configparser():
    """Test GCPConfig initialized from a ConfigParser object"""
    PROJECT = 'test-project'
    REGION = 'test-region'
    ZONE = 'test-zone'
    NETWORK = 'network'
    SUBNET = 'subnet'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_GCP_PROJECT: PROJECT,
                                    CFG_CP_GCP_REGION: REGION,
                                    CFG_CP_GCP_ZONE: ZONE,
                                    CFG_CP_GCP_NETWORK: NETWORK,
                                    CFG_CP_GCP_SUBNETWORK: SUBNET}

    cfg = GCPConfig.create_from_cfg(confpars)
    assert cfg.cloud == CSP.GCP
    assert cfg.project == PROJECT
    assert cfg.region == REGION
    assert cfg.zone == ZONE
    assert cfg.network == NETWORK
    assert cfg.subnet == SUBNET
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


def test_gcpconfig_from_configparser_missing():
    """Test missing required parameters are reported when initializing
    GCPConfig from a ConfigParser object"""
    REQUIRED_PARAMS = [CFG_CP_GCP_PROJECT, CFG_CP_GCP_REGION, CFG_CP_GCP_ZONE]
    with pytest.raises(ValueError) as err:
        cfg = GCPConfig.create_from_cfg(configparser.ConfigParser())

    for param in REQUIRED_PARAMS:
        assert 'Missing ' + param in str(err.value)


def test_gcpconfig_from_configparser_errors():
    """Test that incorrect parameter values in ConfigParser are properly
    reported"""
    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_GCP_PROJECT: 'inval!d-PROJECT',
                                    CFG_CP_GCP_REGION: 'invalie-rEg!on',
                                    CFG_CP_GCP_ZONE: 'inavlid-zone-@#$'}

    with pytest.raises(ValueError) as err:
        cfg = GCPConfig.create_from_cfg(confpars)

    # test that each invalid parameter value is reported
    errors = str(err.value).split('\n')
    for key in confpars[CFG_CLOUD_PROVIDER]:
        assert [message for message in errors if key in message and 'invalid value' in message and confpars[CFG_CLOUD_PROVIDER][key] in message]


def test_awsconfig():
    """Test AWSConfig defaults"""
    REGION = 'test-region'
    cfg = AWSConfig(region = REGION)
    assert cfg.region == REGION
    assert not cfg.vpc
    assert not cfg.subnet
    assert not cfg.security_group
    assert not cfg.key_pair
    assert not cfg.job_role
    assert not cfg.instance_role
    assert not cfg.batch_service_role
    assert not cfg.spot_fleet_role
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


def test_awsconfig_from_configparser():
    """Test AWSConfig initialized from a ConfigParser object"""
    REGION = 'test-region'
    VPC = 'test-vpc'
    SUBNET = 'test-subnet'
    SECURITY_GROUP = 'test-security-group'
    KEY_PAIR = 'test-key-pair'
    JOB_ROLE = 'arn:aws:iam::test-job-role'
    INSTANCE_ROLE = 'arn:aws:iam::test-instance-role'
    BATCH_SERV_ROLE = 'arn:aws:iam::test-batch-service-role'
    SPOT_FLEET_ROLE = 'arn:aws:iam::test-spot-fleet-role'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_AWS_REGION: REGION,
                                    CFG_CP_AWS_VPC: VPC,
                                    CFG_CP_AWS_SUBNET: SUBNET,
                                    CFG_CP_AWS_SECURITY_GROUP: SECURITY_GROUP,
                                    CFG_CP_AWS_KEY_PAIR: KEY_PAIR,
                                    CFG_CP_AWS_JOB_ROLE: JOB_ROLE,
                                    CFG_CP_AWS_INSTANCE_ROLE: INSTANCE_ROLE,
                                    CFG_CP_AWS_BATCH_SERVICE_ROLE: BATCH_SERV_ROLE,
                                    CFG_CP_AWS_SPOT_FLEET_ROLE: SPOT_FLEET_ROLE}
    cfg = AWSConfig.create_from_cfg(confpars)
    assert cfg.region == REGION
    assert cfg.vpc == VPC
    assert cfg.subnet == SUBNET
    assert cfg.security_group == SECURITY_GROUP
    assert cfg.key_pair == KEY_PAIR
    assert cfg.job_role == JOB_ROLE
    assert cfg.instance_role == INSTANCE_ROLE
    assert cfg.batch_service_role == BATCH_SERV_ROLE
    assert cfg.spot_fleet_role == SPOT_FLEET_ROLE
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


def test_awsconfig_from_configparser_missing():
    """Test missing required parameters are reported when initializing
    GCPConfig from a ConfigParser object"""
    REQUIRED_PARAMS = [CFG_CP_AWS_REGION]
    with pytest.raises(ValueError) as err:
        cfg = AWSConfig.create_from_cfg(configparser.ConfigParser())

    for param in REQUIRED_PARAMS:
        assert 'Missing ' + param in str(err.value)


def test_blastconfig(gke_mock):
    """Test BlastConfig defaults"""
    PROGRAM = 'blastp'
    DB = 'testdb'
    QUERIES = 'test-queries'
    NUM_CPUS = 8

    cloud_provider = GCPConfig(project = 'test-project',
                               region = 'test-region',
                               zone = 'test-zone')
    machine_type = 'n1-standard-32'
    
    cfg = BlastConfig(program = PROGRAM,
                      db = DB,
                      queries_arg = QUERIES,
                      cloud_provider = cloud_provider,
                      machine_type = machine_type,
                      num_cpus = NUM_CPUS)

    assert cfg.program == PROGRAM
    assert cfg.db == DB
    assert cfg.queries_arg == QUERIES
    assert cfg.db_source.name == cloud_provider.cloud.name
    assert cfg.batch_len == get_query_batch_size(cfg.program)
    assert not cfg.queries
    assert cfg.options == f'-outfmt {ELB_DFLT_OUTFMT}'
    assert cfg.mem_request
    props = gcp_get_machine_properties(machine_type)
    assert cfg.mem_limit.asGB() == props.memory - SYSTEM_MEMORY_RESERVE
    assert not cfg.taxidlist
    assert cfg.db_mem_margin == ELB_BLASTDB_MEMORY_MARGIN


AWS_INSTANCE_RAM = 120
AWS_INSTANCE_NUM_CPUS = 32

@patch(target='elastic_blast.tuner.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM)))
def test_blastconfig_aws(gke_mock):
    """Test BlastConfig defaults"""
    PROGRAM = 'blastp'
    DB = 'testdb'
    QUERIES = 'test-queries'
    NUM_CPUS = 8

    cloud_provider = AWSConfig(region = 'test-region')
    machine_type = 'm5.x8large'

    cfg = BlastConfig(program = PROGRAM,
                      db = DB,
                      queries_arg = QUERIES,
                      cloud_provider = cloud_provider,
                      machine_type = machine_type,
                      num_cpus = NUM_CPUS)

    assert cfg.program == PROGRAM
    assert cfg.db == DB
    assert cfg.queries_arg == QUERIES
    assert cfg.db_source.name == cloud_provider.cloud.name
    assert cfg.batch_len == get_query_batch_size(cfg.program)
    assert not cfg.queries
    assert cfg.options == f'-outfmt {ELB_DFLT_OUTFMT}'
    assert cfg.mem_request
    assert abs(cfg.mem_limit.asGB() - (AWS_INSTANCE_RAM - SYSTEM_MEMORY_RESERVE) / math.floor(AWS_INSTANCE_NUM_CPUS / NUM_CPUS)) < 1
    assert not cfg.taxidlist
    assert cfg.db_mem_margin == ELB_BLASTDB_MEMORY_MARGIN


def test_blastconfig_validation(gke_mock):
    """Test BlastConfig validation"""
    BAD_URI = 'gs://@BadURI!'
    cfg = BlastConfig(program = 'blastp',
                      db = 'testdb',
                      queries_arg = BAD_URI,
                      cloud_provider = GCPConfig(project = 'test-project',
                                                 region = 'test-region',
                                                 zone = 'test-zone'),
                      machine_type = 'n1-standard-32',
                      num_cpus = 16)
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert errors
    assert [message for message in errors if BAD_URI in message]


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 120)))
def test_blastconfig_from_configparser(gke_mock):
    """Test BlastConfig initialized from a ConfigParser object"""
    PROGRAM = 'blastp'
    DB = 'testdb'
    QUERIES = 'test-queries'
    DB_SOURCE = 'GCP'
    BATCH_LEN = 5000
    OPTIONS = f'test options -outfmt {ELB_DFLT_OUTFMT}'
    MEM_REQUEST = '1.3G'
    MEM_LIMIT = '21.9G'
    DB_MEM_MARGIN = 91.6

    confpars = configparser.ConfigParser()
    confpars[CFG_BLAST] = {CFG_BLAST_PROGRAM: PROGRAM,
                           CFG_BLAST_DB: DB,
                           CFG_BLAST_QUERY: QUERIES,
                           CFG_BLAST_DB_SRC: DB_SOURCE,
                           CFG_BLAST_BATCH_LEN: str(BATCH_LEN),
                           CFG_BLAST_OPTIONS: OPTIONS,
                           CFG_BLAST_MEM_REQUEST: MEM_REQUEST,
                           CFG_BLAST_MEM_LIMIT: MEM_LIMIT,
                           CFG_BLAST_DB_MEM_MARGIN: str(DB_MEM_MARGIN)}

    cfg = BlastConfig.create_from_cfg(confpars,
                                      cloud_provider = AWSConfig(region = 'test-region'),
                                      machine_type = 'test-machine-type',
                                      num_cpus = 9)

    assert cfg.program == PROGRAM
    assert cfg.db == DB
    assert cfg.queries_arg == QUERIES
    assert cfg.db_source == DBSource[DB_SOURCE]
    assert cfg.batch_len == BATCH_LEN
    assert not cfg.queries
    assert cfg.options == OPTIONS
    assert cfg.mem_limit == MEM_LIMIT
    assert cfg.mem_request == MEM_REQUEST
    # taxid list is later parsed from BLAST options
    assert not cfg.taxidlist
    assert cfg.db_mem_margin == DB_MEM_MARGIN
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


def test_blastconfig_from_configparser_missing():
    """Test BlastConfig initialization from a ConfigParser object with missing
    required parameters"""
    REQUIRED_PARAMS = [CFG_BLAST_PROGRAM, CFG_BLAST_DB, CFG_BLAST_QUERY]
    with pytest.raises(ValueError) as err:
        cfg = BlastConfig.create_from_cfg(configparser.ConfigParser(),
                                          cloud_provider = AWSConfig(region = 'test-region'),
                                          machine_type = 'test-machine-type')


    for param in REQUIRED_PARAMS:
        assert 'Missing ' + param in str(err.value)
                                    

@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 120)))
def test_blastconfig_from_configparser_errors():
    """Test that incorrect parameter values in ConfigParser are properly
    reported"""
    PROGRAM = 'some-program'
    DB_SOURCE = 'some-db-source'
    BATCH_LEN = -5
    MEM_LIMIT = '5'
    MEM_REQUEST = -5
    DB_MEM_MARGIN = 'margin'

    confpars = configparser.ConfigParser()
    confpars[CFG_BLAST] = {CFG_BLAST_PROGRAM: PROGRAM,
                           CFG_BLAST_DB: 'some-db',
                           CFG_BLAST_QUERY: 'some-query',
                           CFG_BLAST_DB_SRC: DB_SOURCE,
                           CFG_BLAST_BATCH_LEN: str(BATCH_LEN),
                           CFG_BLAST_MEM_LIMIT: str(MEM_LIMIT),
                           CFG_BLAST_MEM_REQUEST: str(MEM_REQUEST),
                           CFG_BLAST_DB_MEM_MARGIN: str(DB_MEM_MARGIN)}


    with pytest.raises(ValueError) as err:
        cfg = BlastConfig.create_from_cfg(confpars,
                                          cloud_provider = AWSConfig(region = 'test-region'),
                                          machine_type = 'some-machine-type')

    # test that each invalid parameter value is reported
    errors = str(err.value).split('\n')
    for key in [CFG_BLAST_PROGRAM,
                CFG_BLAST_DB_SRC,
                CFG_BLAST_BATCH_LEN,
                CFG_BLAST_MEM_LIMIT,
                CFG_BLAST_MEM_REQUEST,
                CFG_BLAST_DB_MEM_MARGIN]:
        assert [message for message in errors if key in message and 'invalid value' in message and confpars[CFG_BLAST][key] in message]


def test_clusterconfig_gcp():
    """Test ClusterConfig defaults for GCP"""
    RESULTS = CloudURI('gs://test-results')
    gcp_cfg = GCPConfig(project = 'test-project',
                        region = 'test-region',
                        zone = 'test-zone')
    cfg = ClusterConfig(cloud_provider = gcp_cfg, results = RESULTS)
    assert cfg.name.startswith('elasticblast')
    assert cfg.machine_type == ELB_DFLT_GCP_MACHINE_TYPE
    assert cfg.pd_size == ELB_DFLT_GCP_PD_SIZE
    assert cfg.num_cpus == ELB_DFLT_GCP_NUM_CPUS
    assert cfg.num_nodes == ELB_DFLT_NUM_NODES
    assert cfg.results == RESULTS
    assert not cfg.use_preemptible
    assert not cfg.iops
    assert not cfg.labels
    assert not cfg.use_local_ssd
    assert not cfg.enable_stackdriver
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 120)))
def test_clusterconfig_aws():
    """Test ClusterConfig defaults for AWS"""
    RESULTS = CloudURI('s3://test-results')
    aws_cfg = AWSConfig(region = 'test-region')
    cfg = ClusterConfig(cloud_provider = aws_cfg, results = RESULTS)
    assert cfg.name.startswith('elasticblast')
    assert cfg.results == RESULTS
    assert cfg.machine_type == ELB_DFLT_AWS_MACHINE_TYPE
    assert cfg.pd_size == ELB_DFLT_AWS_PD_SIZE
    assert cfg.num_cpus == ELB_DFLT_AWS_NUM_CPUS
    assert cfg.num_nodes == ELB_DFLT_NUM_NODES
    assert not cfg.use_preemptible
    assert cfg.disk_type == ELB_DFLT_AWS_DISK_TYPE
    assert not cfg.iops
    assert cfg.bid_percentage == int(ELB_DFLT_AWS_SPOT_BID_PERCENTAGE)
    assert not cfg.labels
    assert not cfg.use_local_ssd
    assert not cfg.enable_stackdriver
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 120)))
def test_clusterconfig_aws_arm_instances():
    """Test ClusterConfig defaults for AWS"""
    RESULTS = CloudURI('s3://test-results')
    aws_cfg = AWSConfig(region = 'test-region')
    cfg = ClusterConfig(cloud_provider = aws_cfg, results = RESULTS, machine_type = 'r6gd.8xlarge')
    assert cfg.name.startswith('elasticblast')
    assert cfg.results == RESULTS
    assert cfg.machine_type == 'r6gd.8xlarge'
    assert cfg.pd_size == ELB_DFLT_AWS_PD_SIZE
    assert cfg.num_cpus == ELB_DFLT_AWS_NUM_CPUS
    assert cfg.num_nodes == ELB_DFLT_NUM_NODES
    assert not cfg.use_preemptible
    assert cfg.disk_type == ELB_DFLT_AWS_DISK_TYPE
    assert not cfg.iops
    assert cfg.bid_percentage == int(ELB_DFLT_AWS_SPOT_BID_PERCENTAGE)
    assert not cfg.labels
    assert not cfg.use_local_ssd
    assert not cfg.enable_stackdriver
    errors = []
    cfg.validate(errors, ElbCommand.SUBMIT)
    assert 'not supported by ElasticBLAST' in errors[0]


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(ELB_DFLT_AWS_NUM_CPUS, 120)))
def test_clusterconfig_from_configparser():
    """Test ClusterConfig initialized from a ConfigParser object"""
    RESULTS = 's3://test-bucket'
    NAME = 'test-name'
    MACHINE_TYPE = 'test-machine-type'
    PD_SIZE = 'test-pd-size'
    NUM_CPUS = 10
    NUM_NODES = 5000
    USE_PREEMPTIBLE = 'Yes'
    DISK_TYPE = 'test-disk-type'
    IOPS = 987
    BID_PERC = 45
    LABELS = 'test-labels'
    USE_LOCAL_SSD = 'yes'
    ENABLE_STACKDRIVER = 'true'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLUSTER] = {CFG_CLUSTER_NAME: NAME,
                             CFG_CLUSTER_MACHINE_TYPE: MACHINE_TYPE,
                             CFG_CLUSTER_PD_SIZE: PD_SIZE,
                             CFG_CLUSTER_NUM_CPUS: str(NUM_CPUS),
                             CFG_CLUSTER_NUM_NODES: str(NUM_NODES),
                             CFG_CLUSTER_USE_PREEMPTIBLE: USE_PREEMPTIBLE,
                             CFG_CLUSTER_DISK_TYPE: DISK_TYPE,
                             CFG_CLUSTER_PROVISIONED_IOPS: IOPS,
                             CFG_CLUSTER_BID_PERCENTAGE: BID_PERC,
                             CFG_CLUSTER_LABELS: LABELS,
                             CFG_CLUSTER_EXP_USE_LOCAL_SSD: USE_LOCAL_SSD,
                             CFG_CLUSTER_ENABLE_STACKDRIVER: ENABLE_STACKDRIVER}
    confpars[CFG_BLAST] = {CFG_BLAST_RESULTS: RESULTS}

    cfg = ClusterConfig.create_from_cfg(confpars,
                                        cloud_provider = AWSConfig(region = 'test-region'))

    assert cfg.name == NAME
    assert cfg.machine_type == MACHINE_TYPE
    assert cfg.pd_size == PD_SIZE
    assert cfg.num_cpus == NUM_CPUS
    assert cfg.num_nodes == NUM_NODES
    assert cfg.use_preemptible == True
    assert cfg.disk_type == DISK_TYPE
    assert cfg.iops == IOPS
    assert cfg.bid_percentage == BID_PERC
    assert cfg.labels == LABELS
    assert cfg.use_local_ssd == True
    assert cfg.enable_stackdriver == True
    errors = []
    # caused by use_local_ssd == True
    with pytest.raises(NotImplementedError):
        cfg.validate(errors, ElbCommand.SUBMIT)
    assert not errors


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 120)))
def test_clusterconfig_from_configparser_missing():
    """Test ClusterConfig initialization from a ConfigParser object with
    missing required parameters"""
    REQUIRED_PARAMS = [CFG_BLAST_RESULTS]
    with pytest.raises(ValueError) as err:
        cfg = ClusterConfig.create_from_cfg(configparser.ConfigParser(),
                                            cloud_provider = AWSConfig(region = 'test-region'))

    for param in REQUIRED_PARAMS:
        assert 'Missing ' + param in str(err.value)


def test_clusterconfig_from_configparser_errors():
    """Test that incorrect parameter values in ConfigParser are properly
    reported"""
    confpars = configparser.ConfigParser()
    confpars[CFG_CLUSTER] = {CFG_CLUSTER_NUM_CPUS: '-25',
                             CFG_CLUSTER_NUM_NODES: 'abc',
                             CFG_CLUSTER_BID_PERCENTAGE: '101'}

    with pytest.raises(ValueError) as err:
        cfg = ClusterConfig.create_from_cfg(confpars,
                                            cloud_provider = CSP.AWS)

    # test that each invalid parameter value is reported
    errors = str(err.value).split('\n')
    for key in confpars[CFG_CLUSTER].keys():
        assert [message for message in errors if key in message and 'invalid value' in message and confpars[CFG_CLUSTER][key] in message]


def test_ElasticBlastConfig_init_errors():
    """Test that __init__ method arguments are checked"""
    with pytest.raises(AttributeError) as err:
        cfg = ElasticBlastConfig()
    assert 'task parameter must be specified' in str(err.value)

    with pytest.raises(AttributeError) as err:
        cfg = ElasticBlastConfig(5)
    assert 'two positional arguments' in str(err.value)
    assert 'ConfigParser object' in str(err.value)

    with pytest.raises(AttributeError) as err:
        cfg = ElasticBlastConfig(configparser.ConfigParser(), False, 5)
    assert 'two positional arguments' in str(err.value)
    assert 'ConfigParser object' in str(err.value)

    with pytest.raises(AttributeError) as err:
        cfg = ElasticBlastConfig(configparser.ConfigParser(), results = 's3://results')
    assert 'task parameter must be specified' in str(err.value)

    with pytest.raises(AttributeError) as err:
        cfg = ElasticBlastConfig(aws_region = 'some-region', results = 's3://results')
    assert 'task parameter must be specified' in str(err.value)


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 128)))
@patch(target='elastic_blast.tuner.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 128)))
def test_validate_too_many_cpus(gke_mock):
    """Test that requesting too many CPUs is reported"""
    cfg = ElasticBlastConfig(aws_region = 'test-region',
                             program = 'blastp',
                             db = 'testdb',
                             queries = 'test-query.fa',
                             results = 's3://results',
                             task = ElbCommand.SUBMIT)
    cfg.cluster.num_cpus = 128

    with pytest.raises(UserReportError) as err:
        cfg.validate(ElbCommand.SUBMIT)
    assert  re.search(r'number of CPUs [\w "]* exceeds', str(err.value))


@patch(target='elastic_blast.elb_config.gcp_get_machine_properties', new=MagicMock(return_value=InstanceProperties(32, 128)))
@patch(target='elastic_blast.tuner.gcp_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM+8)))
def test_get_max_concurrent_blast_jobs_gcp(gke_mock):
    PROJECT = 'some-project'
    REGION = 'some-region'
    ZONE = 'some-zone'
    QUERY = 'some-query'
    DB = 'testdb'
    PROGRAM = 'blastp'
    RESULTS = 'gs://results'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_GCP_PROJECT: PROJECT,
                                    CFG_CP_GCP_REGION: REGION,
                                    CFG_CP_GCP_ZONE: ZONE}

    confpars[CFG_CLUSTER] = {CFG_CLUSTER_MACHINE_TYPE: ELB_DFLT_GCP_MACHINE_TYPE,
            CFG_CLUSTER_NUM_CPUS: 15,
            CFG_CLUSTER_NUM_NODES: 5
            }

    confpars[CFG_BLAST] = {CFG_BLAST_QUERY: QUERY,
                           CFG_BLAST_DB: DB,
                           CFG_BLAST_PROGRAM: PROGRAM,
                           CFG_BLAST_RESULTS: RESULTS}

    cfg = ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    cfg.validate(ElbCommand.SUBMIT)
    n = cfg.get_max_number_of_concurrent_blast_jobs()
    assert n == 10
    assert cfg.cluster.instance_memory.asGB() == 128
    assert cfg.blast.mem_limit.asGB() == 126
    assert cfg.blast.mem_request.asGB() == 0.5


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM+8)))
@patch(target='elastic_blast.tuner.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM+8)))
def test_get_max_concurrent_blast_jobs_aws(gke_mock):
    PROJECT = 'some-project'
    REGION = 'some-region'
    ZONE = 'some-zone'
    QUERY = 'some-query'
    DB = 'testdb'
    PROGRAM = 'blastp'
    RESULTS = 's3://results'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_AWS_REGION: ELB_DFLT_AWS_REGION}
    confpars[CFG_CLUSTER] = {CFG_CLUSTER_MACHINE_TYPE: ELB_DFLT_AWS_MACHINE_TYPE,
            CFG_CLUSTER_NUM_CPUS: 16,
            CFG_CLUSTER_NUM_NODES: 5
            }

    confpars[CFG_BLAST] = {CFG_BLAST_QUERY: QUERY,
                           CFG_BLAST_DB: DB,
                           CFG_BLAST_PROGRAM: PROGRAM,
                           CFG_BLAST_RESULTS: RESULTS}

    cfg = ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    cfg.validate(ElbCommand.SUBMIT)
    n = cfg.get_max_number_of_concurrent_blast_jobs()
    assert n == 10
    assert cfg.cluster.instance_memory.asGB() == 128
    assert cfg.blast.mem_limit.asGB() == 63
    assert cfg.blast.mem_request.asGB() == 0.5


def test_ElasticBlastConfig_from_configparser(gke_mock):
    """Test creating ElasticBlastConfig from a ConfigParser object"""
    PROJECT = 'some-project'
    REGION = 'some-region'
    ZONE = 'some-zone'
    QUERY = 'some-query'
    DB = 'testdb'
    PROGRAM = 'blastp'
    RESULTS = 'gs://results'

    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_GCP_PROJECT: PROJECT,
                                    CFG_CP_GCP_REGION: REGION,
                                    CFG_CP_GCP_ZONE: ZONE}

    confpars[CFG_BLAST] = {CFG_BLAST_QUERY: QUERY,
                           CFG_BLAST_DB: DB,
                           CFG_BLAST_PROGRAM: PROGRAM,
                           CFG_BLAST_RESULTS: RESULTS}

    cfg = ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    assert cfg.gcp.project == PROJECT
    assert cfg.gcp.region == REGION
    assert cfg.gcp.zone == ZONE
    assert cfg.blast.queries_arg == QUERY
    assert cfg.blast.db == DB
    assert cfg.blast.program == PROGRAM
    assert cfg.cluster.results == RESULTS


def test_ElasticBlastConfig_from_configparser_wrong_params():
    """Test initializing ElasticBlastConfig object from a ConfigParser object
    with wrong section or parameter names results in an exception"""
    confpars = configparser.ConfigParser()
    confpars[CFG_CLOUD_PROVIDER] = {CFG_CP_GCP_PROJECT: 'some-project',
                                    CFG_CP_GCP_REGION: 'region',
                                    CFG_CP_GCP_ZONE: 'zone'}

    # a correct parameter in an incorrect section
    confpars[CFG_BLAST] = {CFG_BLAST_QUERY: 'some-queries',
                           CFG_BLAST_DB: 'some-db',
                           CFG_BLAST_PROGRAM: 'blastp',
                           CFG_BLAST_RESULTS: 'gs://results',
                           CFG_CLUSTER_NUM_CPUS: '4'}

    with pytest.raises(UserReportError) as err:
        ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    assert err.value.returncode == INPUT_ERROR
    assert 'Unrecognized configuration parameter' in err.value.message
    assert CFG_CLUSTER_NUM_CPUS in err.value.message
    assert CFG_BLAST in err.value.message

    confpars[CFG_BLAST] = {CFG_BLAST_QUERY: 'some-queries',
                           CFG_BLAST_DB: 'some-db',
                           CFG_BLAST_PROGRAM: 'blastp',
                           CFG_BLAST_RESULTS: 'gs://results'}

    # misspelled section name
    confpars['clustr'] = {CFG_CLUSTER_NUM_CPUS: '4'}

    with pytest.raises(UserReportError) as err:
        ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    assert err.value.returncode == INPUT_ERROR
    assert 'Unrecognized configuration parameter' in err.value.message
    assert CFG_CLUSTER_NUM_CPUS in err.value.message
    assert 'clustr' in err.value.message

    del confpars['clustr']

    # a non-existent parameter in a non-existent section
    confpars['wrong-section'] = {'wrong-param': 'some-value'}

    with pytest.raises(UserReportError) as err:
        ElasticBlastConfig(confpars, task = ElbCommand.SUBMIT)
    assert err.value.returncode == INPUT_ERROR
    assert 'Unrecognized configuration parameter' in err.value.message
    assert 'wrong-section' in err.value.message
    assert 'wrong-param' in err.value.message


def test_validate_too_little_memory(gke_mock):
    """Test that selecting a machine-type with not too small memory for a
    database results in an error message"""
    DB = 'gs://bucket/largedb'

    # bytes-to-cache is over 900GB
    DB_METADATA = """{
      "dbname": "largedb",
      "version": "1.1",
      "dbtype": "Protein",
      "description": "Some large database",
      "number-of-letters": 180911227,
      "number-of-sequences": 477327,
      "files": [
        "gs://blast-db/2021-09-28-01-05-02/largedb.ppi",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pos",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pog",
        "gs://blast-db/2021-09-28-01-05-02/largedb.phr",
        "gs://blast-db/2021-09-28-01-05-02/largedb.ppd",
        "gs://blast-db/2021-09-28-01-05-02/largedb.psq",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pto",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pin",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pot",
        "gs://blast-db/2021-09-28-01-05-02/largedb.ptf",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pdb"
      ],
      "last-updated": "2021-09-19T00:00:00",
      "bytes-total": 353839003,
      "bytes-to-cache": 999185207299,
      "number-of-volumes": 1
    }
    """

    gke_mock.cloud.storage[f'{DB}-prot-metadata.json'] = DB_METADATA

    with pytest.raises(UserReportError) as err:
        cfg = ElasticBlastConfig(program = 'blastp',
                                 queries = 'some-query.fa',
                                 db = DB,
                                 results = 'gs://results',
                                 gcp_project = 'some-project',
                                 gcp_region = 'some-region',
                                 gcp_zone = 'some-zone',
                                 task = ElbCommand.SUBMIT)
        cfg.validate()
    assert err.value.returncode == INPUT_ERROR
    assert re.search(r'BLAST database [\w/:"]* memory requirements exceed memory available on selected machine type', err.value.message)


def test_missing_ncbi_db_metadata(gke_mock):
    """Test that an exception is raised for a missing NCBI database metadata file"""
    DB = 'some-non-existant-ncbi-db'

    with pytest.raises(UserReportError) as err:
        cfg = ElasticBlastConfig(program = 'blastp',
                                 queries = 'some-query.fa',
                                 db = DB,
                                 results = 'gs://results',
                                 gcp_project = 'some-project',
                                 gcp_region = 'some-region',
                                 gcp_zone = 'some-zone',
                                 task = ElbCommand.SUBMIT)
        cfg.validate()
    assert err.value.returncode == BLASTDB_ERROR
    assert f'Metadata for BLAST database "{DB}" was not found' in err.value.message


def test_missing_user_db_metadata(gke_mock):
    """Test that a missing user database metadata file is fine"""
    DB = 'gs://bucket/some-non-existant-user-db'

    cfg = ElasticBlastConfig(program = 'blastp',
                             queries = 'some-query.fa',
                             db = DB,
                             results = 'gs://results',
                             gcp_project = 'some-project',
                             gcp_region = 'some-region',
                             gcp_zone = 'some-zone',
                             task = ElbCommand.SUBMIT)
    cfg.validate()


@patch(target='elastic_blast.elb_config.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM+8)))
@patch(target='elastic_blast.tuner.aws_get_machine_properties', new=MagicMock(return_value=InstanceProperties(AWS_INSTANCE_NUM_CPUS, AWS_INSTANCE_RAM+8)))
def test_incorrect_db_mol_type(gke_mock):
    """Test that incorrect BLAST database molecule type is reported"""

    DB = 'some-db'
    DB_MOL_TYPE = 'Protein'
    PROGRAM = 'blastn'

    DB_METADATA = '{' + f"""
      "dbname": "{DB}",
      "version": "1.1",
      "dbtype": "{DB_MOL_TYPE}",
      "description": "Some large database",
      "number-of-letters": 180911227,
      "number-of-sequences": 477327,
      "files": [
        "gs://blast-db/2021-09-28-01-05-02/largedb.ppi",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pos",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pog",
        "gs://blast-db/2021-09-28-01-05-02/largedb.phr",
        "gs://blast-db/2021-09-28-01-05-02/largedb.ppd",
        "gs://blast-db/2021-09-28-01-05-02/largedb.psq",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pto",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pin",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pot",
        "gs://blast-db/2021-09-28-01-05-02/largedb.ptf",
        "gs://blast-db/2021-09-28-01-05-02/largedb.pdb"
      ],
      "last-updated": "2021-09-19T00:00:00",
      "bytes-total": 353839003,
      "bytes-to-cache": 185207299,
      "number-of-volumes": 1
""" + '}'

    gke_mock.cloud.storage[f's3://ncbi-blast-databases/000/{DB}-{DB_MOL_TYPE.lower()[:4]}-metadata.json'] = DB_METADATA
    assert MolType[DB_MOL_TYPE.upper()] != ElbSupportedPrograms().get_db_mol_type(PROGRAM)

    with pytest.raises(UserReportError) as err:
        cfg = ElasticBlastConfig(program = PROGRAM,
                                 queries = 'some-query.fa',
                                 db = DB,
                                 results = 's3://results',
                                 aws_region = 'some-region',
                                 task = ElbCommand.SUBMIT)
        cfg.validate()
    assert err.value.returncode == BLASTDB_ERROR
    assert f'database molecular type' in err.value.message

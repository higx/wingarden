from common_fixtures import *  # NOQA
from cattle import ClientApiError

SP_CREATE = "storagepool.create"
VOLUME_CREATE = "volume.create"
CONTAINER_STATES = ["running", "stopped", "stopping"]


def more_hosts(context):
    host2 = register_simulated_host(context)
    host3 = register_simulated_host(context)
    return context.host, host2, host3


def from_context(context):
    return context.client, context.agent_client, context.host


def add_storage_pool(context, host_uuids=None, **kwargs):
    client, agent_client, host = from_context(context)
    sp_name = 'new-sp-%s' % random_str()
    if not host_uuids:
        host_uuids = [host.uuid]

    create_sp_event(client, agent_client, context,
                    sp_name, sp_name, SP_CREATE, host_uuids, sp_name, **kwargs)
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    return storage_pool


def create_new_agent(super_client, project):
    scope = 'io.cattle.platform.agent.connection.simulator' \
            '.AgentConnectionSimulator'
    uri = 'sim://{}'.format(random_str())
    data = {scope: {}}
    account_id = get_plain_id(super_client, project)
    data[scope]['agentResourcesAccountId'] = account_id
    data['agentResourcesAccountId'] = account_id
    agent = super_client.create_agent(uri=uri, data=data)
    agent = super_client.wait_success(agent)

    assert agent.state == "active"
    account = agent.account()
    creds = filter(lambda x: x.kind == 'agentApiKey', account.credentials())
    agent_client = api_client(creds[0].publicValue, creds[0].secretValue)
    return agent, account, agent_client


def test_single_instance_rw_new_disks(super_client, new_context):
    disks = [
        {
            'size': '2g',
        },
        {
            'name': 'foo',
            'size': '2g',
            'root': True,
        },
    ]

    single_instance_rw_test(super_client, new_context, disks)


def test_single_instance_rw_preexisting_volume(super_client, new_context):
    client = new_context.client
    name = 'exists-%s' % random_str()
    sp_name = 'storage-%s' % random_str()
    volume = client.create_volume(name=name, driver=sp_name)
    volume = client.wait_success(volume)
    assert volume.state == 'inactive'
    disks = [
        {
            'name': name,
            'size': '2g',
        },
    ]

    single_instance_rw_test(super_client, new_context, disks, sp_name=sp_name)


def single_instance_rw_test(super_client, new_context, disks, sp_name=None):
    client, agent_client, host = from_context(new_context)
    if not sp_name:
        sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host.uuid, host2.uuid]

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    vm = _create_virtual_machine(client, new_context, name=random_str(),
                                 volumeDriver=sp_name,
                                 userdata='hi', vcpu=2, memoryMb=42,
                                 disks=disks)
    vm = client.wait_success(vm)
    assert vm.state == 'running'

    svm = super_client.reload(vm)

    for k, vol_id in svm.dataVolumeMounts.__dict__.iteritems():
        create_mount(vol_id, vm, client, super_client)

    data_volumes = []
    for dv in svm.dataVolumes:
        if not dv.startswith('/'):
            vol_name = dv.split(':')[0]
            data_volumes.append('%s:/%s' % (vol_name, vol_name))

    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=data_volumes)
    c = client.wait_transitioning(c)
    assert c.transitioning == 'error'
    assert c.transitioningMessage.startswith('Allocation failed: Volume')
    assert c.state == 'error'

    vm = client.wait_success(vm.stop())
    client.wait_success(vm.remove())

    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=data_volumes)
    c = client.wait_success(c)
    assert c.state == 'running'


def _create_virtual_machine(client, context, **kw):
    args = {
        'accountId': context.project.id,
        'imageUuid': context.image_uuid,
    }
    args.update(kw)

    return client.create_virtual_machine(**args)


def test_anonymous_volume_in_pool(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host_uuids = [host.uuid]

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name)
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'

    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=['/foo', '/bar:ro'],
                                volumeDriver=sp_name)
    c = client.wait_success(c)
    assert c.state == 'running'
    assert len(c.dataVolumeMounts) == 2
    assert set(c.dataVolumeMounts.keys()) == {'/foo', '/bar:ro'}


def test_single_host_rw(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host.uuid, host2.uuid]

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)

    data_volume_mounts = {'/con/path': v1.id}
    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumeMounts=data_volume_mounts)
    c = client.wait_success(c)
    assert c.state == 'running'

    v1 = client.wait_success(v1)
    create_mount(v1.id, c, client, super_client)
    sps = v1.storagePools()
    assert len(sps) == 1
    assert sps[0].id == storage_pool.id
    assert v1.accessMode == 'singleHostRW'

    # Deactivate the host that c was deployed to
    c1_host = c.hosts()[0]
    if c1_host.uuid == host.uuid:
        client.wait_success(host.deactivate())
    else:
        client.wait_success(host2.deactivate())

    c2 = client.create_container(imageUuid=new_context.image_uuid,
                                 dataVolumes=['%s:/test/it' % v1.name])
    c2 = client.wait_transitioning(c2)
    assert c2.transitioning == 'error'
    assert c2.transitioningMessage.startswith('Allocation failed: Volume')
    assert c2.state == 'error'

    c = client.wait_success(c.stop())

    c3 = client.create_container(imageUuid=new_context.image_uuid,
                                 dataVolumes=['%s:/test/it' % v1.name])
    c3 = client.wait_success(c3)


def test_single_host_race_condition(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host.uuid, host2.uuid]

    d1 = super_client.create_storageDriver(name=sp_name)
    d1 = super_client.wait_success(d1)
    d1 = super_client.update(d1, data={'fields':
                                       {'volumeAccessMode':
                                        'singleHostRW'}})
    d1 = super_client.wait_success(d1)
    assert d1.data["fields"]["volumeAccessMode"] == "singleHostRW"

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    storage_pool = super_client.update(storage_pool, storageDriverId=d1.id)
    storage_pool = super_client.wait_success(storage_pool)

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)
    v1 = super_client.update(v1, storageDriverId=d1.id)
    v1 = client.wait_success(v1)

    launch_config = {"volumeDriver": sp_name,
                     "dataVolumes": [v1.name + ":/test"],
                     "imageUuid": new_context.image_uuid,
                     "stdinOpen": True
                     }
    scale = 20

    service, stack = create_env_and_svc(client, launch_config,
                                        scale)
    stack.activateservices()
    service = client.wait_success(service, 300)
    assert service.state == "active"
    container_list = get_service_container_list(super_client, service)
    host_id = container_list[0].hostId
    for container in container_list:
        assert container.hostId == host_id


def create_mount(vol_id, container, client, super_client):
    mount = super_client.create_mount(volumeId=vol_id,
                                      instanceId=container.id,
                                      accountId=container.accountId)
    mount = super_client.wait_success(mount)
    return client.reload(container), mount


def test_storage_pool_update(new_context, super_client):
    client = new_context.client
    sp = add_storage_pool(new_context)

    original_agent = super_client.list_agent(accountId=new_context.agent.id)[0]
    assert super_client.reload(sp).agentId == original_agent.id

    new_agent, new_agent_account, new_client = \
        create_new_agent(super_client, new_context.project)

    uuids = [new_context.host.uuid]
    create_sp_event(client, new_client, new_context, sp.name, sp.name,
                    SP_CREATE, uuids, sp.name, new_agent_account)
    assert super_client.wait_success(sp).agentId == new_agent.id
    sp = client.wait_success(sp)
    assert sp.state == 'active'


def test_storage_pool_agent_delete(new_context, super_client):
    client = new_context.client
    sp = add_storage_pool(new_context)

    original_agent = super_client.list_agent(accountId=new_context.agent.id)[0]

    original_agent = super_client.wait_success(original_agent.deactivate())
    original_agent = super_client.wait_success(original_agent.remove())

    sp = client.reload(sp)
    assert sp.state == 'active'


def test_multiple_sp_volume_schedule(new_context):
    # Tests that when a host has more than one storage pool (one local, one
    # shared), and a container is scheduled to it, the root volume can be
    # properly scheduled.
    client = new_context.client
    add_storage_pool(new_context)

    # The allocation bug that caused this issue is much more likely to occur
    # when two containers are created back-to-back
    c = client.create_container(imageUuid=new_context.image_uuid,
                                networkMode=None)
    c2 = client.create_container(imageUuid=new_context.image_uuid,
                                 networkMode=None)

    c = client.wait_success(c)
    assert c is not None
    vols = c.volumes()
    assert len(vols) == 1
    vol_pools = vols[0].storagePools()
    assert len(vol_pools) == 1
    assert vol_pools[0].kind == 'sim'

    c2 = client.wait_success(c2)
    assert c2 is not None
    vols = c2.volumes()
    assert len(vols) == 1
    vol_pools = vols[0].storagePools()
    assert len(vol_pools) == 1
    assert vol_pools[0].kind == 'sim'


def test_finding_shared_volumes(new_context):
    # Tests that when a named is specified in dataVolumes and a volume of
    # that name already exists in a shared storage pool, the pre-existing
    # volume is used
    client, agent_client, host = from_context(new_context)
    storage_pool = add_storage_pool(new_context)
    sp_name = storage_pool.name
    name = random_str()
    uri = '/foo/bar'
    create_volume_event(client, agent_client, new_context, VOLUME_CREATE,
                        name, driver=sp_name, uri=uri)
    volume = wait_for(lambda: volume_wait(client, name))
    volume = wait_for(lambda: volume_in_sp(client, volume, storage_pool))

    path = '/container/path'

    # Previously created volume should show up in dataVolumeMounts
    data_volumes = ['%s:%s' % (name, path)]
    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=data_volumes)
    c = client.wait_success(c)
    assert c.state == 'running'
    assert c.dataVolumeMounts[path] == volume.id

    # Same behavior if volumeDriver == local
    c = client.create_container(imageUuid=new_context.image_uuid,
                                volumeDriver='local',
                                dataVolumes=data_volumes)
    c = client.wait_success(c)
    assert c.state == 'running'
    assert c.dataVolumeMounts[path] == volume.id

    # Create another storage pool and add a volume of the same name to it
    storage_pool = add_storage_pool(new_context)
    sp_name2 = storage_pool.name
    uri = '/foo/bar'
    create_volume_event(client, agent_client, new_context, VOLUME_CREATE,
                        name, driver=sp_name2, uri=uri)
    volume2 = wait_for(lambda: volume_in_sp_by_name_wait(name, storage_pool))
    assert volume2.id != volume.id

    # Container should not create successfully because name is ambiguous
    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=data_volumes)
    with pytest.raises(ClientApiError):
        client.wait_success(c)

    # Even if the volume driver is specified, should fail
    c = client.create_container(imageUuid=new_context.image_uuid,
                                volumeDriver=sp_name2,
                                dataVolumes=data_volumes)
    with pytest.raises(ClientApiError):
        client.wait_success(c)


def test_data_volume_mounts(new_context):
    client, agent_client, host = from_context(new_context)
    storage_pool = add_storage_pool(new_context)
    sp_name = storage_pool.name
    external_id = random_str()
    uri = '/foo/bar'
    create_volume_event(client, agent_client, new_context, VOLUME_CREATE,
                        external_id, driver=sp_name, uri=uri)
    volume = wait_for(lambda: volume_wait(client, external_id))
    volume = wait_for(lambda: volume_in_sp(client, volume, storage_pool))

    data_volume_mounts = {'/somedir': volume.id}
    c = client.create_container(imageUuid=new_context.image_uuid,
                                volumeDriver='local',
                                dataVolumeMounts=data_volume_mounts)
    c = client.wait_success(c, timeout=240)
    assert c.state == 'running'
    assert c.dataVolumes[0] == '%s:/somedir' % external_id


def test_volume_create(new_context):
    client, agent_client, host = from_context(new_context)
    storage_pool = add_storage_pool(new_context)
    sp_name = storage_pool.name
    add_storage_pool(new_context)

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)
    sps = v1.storagePools()
    assert len(sps) == 1
    assert sps[0].id == storage_pool.id

    # Create a volume with a driver that cattle doesn't know about
    v2 = client.create_volume(name=random_str(), driver='driver-%s' %
                                                        random_str())
    v2 = client.wait_success(v2)

    data_volume_mounts = {'/con/path': v1.id,
                          '/con/path2': v2.id}
    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumeMounts=data_volume_mounts)
    c = client.wait_success(c)
    assert c.state == 'running'

    v1 = client.wait_success(v1)
    sps = v1.storagePools()
    assert len(sps) == 1
    assert sps[0].id == storage_pool.id

    v2 = client.wait_success(v2)
    sps = v2.storagePools()
    assert len(sps) == 1
    assert sps[0].kind == 'sim'

    # Create a new, unmapped volume, assign to container via dataVolumes
    # Should be translated to a dataVolumeMount entry.
    v3 = client.create_volume(name=random_str(), driver=sp_name)
    v3 = client.wait_success(v3)

    c = client.create_container(imageUuid=new_context.image_uuid,
                                dataVolumes=['%s:/foo' % v3.name])
    c = client.wait_success(c)
    assert c.state == 'running'
    assert c.dataVolumeMounts['/foo'] == v3.id
    v3 = client.wait_success(v3)
    sps = v3.storagePools()
    assert len(sps) == 1
    assert sps[0].id == storage_pool.id


def create_and_map_volume(client, context):
    name = random_str()
    v = client.create_volume(name=name, driver='local')
    v = client.wait_success(v)
    c = client.wait_success(client.create_container(
        imageUuid=context.image_uuid,
        dataVolumeMounts={'/foo': v.id}))
    assert c.state == 'running'
    assert c.dataVolumeMounts['/foo'] == v.id
    return name, v


def test_volume_create_failed_allocation(new_context):
    client, agent_client, host = from_context(new_context)
    storage_pool = add_storage_pool(new_context)
    sp_name = storage_pool.name
    add_storage_pool(new_context)

    v1 = client.wait_success(client.create_volume(name=random_str(),
                                                  driver=sp_name))
    assert v1.state == 'inactive'

    # Will fail because new_host is not in the storage_pool that v1 belongs to
    new_host = register_simulated_host(new_context)
    data_volume_mounts = {'/con/path': v1.id}
    with pytest.raises(ClientApiError) as e:
        c = client.create_container(imageUuid=new_context.image_uuid,
                                    requestedHostId=new_host.id,
                                    dataVolumeMounts=data_volume_mounts)
        client.wait_success(c)
    assert 'must have exactly these pool' in e.value.message

    # Put two volumes from mutually exclusive storage pools onto a container
    # and it should fail to find placement
    sp2 = add_storage_pool(new_context, [new_host.uuid])
    v2 = client.create_volume(name=random_str(), driver=sp2.name)
    v2 = client.wait_success(v2)
    assert v1.state == 'inactive'
    data_volume_mounts['/con/path2'] = v2.id
    with pytest.raises(ClientApiError) as e:
        c = client.create_container(imageUuid=new_context.image_uuid,
                                    dataVolumeMounts=data_volume_mounts)
        client.wait_success(c)
    assert e.value.message.startswith('Allocation failed')


def test_external_volume_event(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    storage_pool = add_storage_pool(new_context)
    sp_name = storage_pool.name
    external_id = random_str()
    uri = '/foo/bar'

    create_volume_event(client, agent_client, new_context, VOLUME_CREATE,
                        external_id, driver=sp_name, uri=uri)

    volume = wait_for(lambda: volume_wait(client, external_id))
    volume = wait_for(lambda: volume_in_sp(client, volume, storage_pool))
    assert volume.state == 'inactive'
    assert volume.externalId == external_id
    assert volume.name == external_id
    assert volume.driver == sp_name
    assert volume.uri == uri
    assert volume.isHostPath is False
    super_volume = super_client.by_id('volume', volume.id)
    assert super_volume.deviceNumber == -1
    assert super_volume.format == 'docker'

    # Send event again to ensure two volumes are not created
    create_volume_event(client, agent_client, new_context,
                        VOLUME_CREATE, external_id, driver=sp_name, uri=uri)
    volumes = client.list_volume(externalId=external_id)
    assert len(volumes) == 1


def test_external_storage_pool_event(new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'convoy-%s' % random_str()

    # Create a new storage pool with a single host
    uuids = [host.uuid]
    create_sp_event(client, agent_client, new_context,
                    sp_name, sp_name, SP_CREATE, uuids, sp_name)
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    assert storage_pool.externalId == sp_name
    assert storage_pool.name == sp_name
    assert storage_pool.driverName == sp_name
    hosts = wait_for(lambda: wait_host_count(storage_pool, 1))
    assert len(hosts) == 1
    assert hosts[0].uuid == host.uuid

    # Send event again to ensure a second storage pool is not created
    create_sp_event(client, agent_client, new_context,
                    sp_name, sp_name, SP_CREATE, uuids, sp_name)

    # Add a second host
    host2 = register_simulated_host(new_context)
    uuids.append(host2.uuid)
    create_sp_event(client, agent_client, new_context,
                    sp_name,
                    sp_name, SP_CREATE, uuids, sp_name)
    hosts = wait_for(lambda: wait_host_count(storage_pool, 2))
    host_ids = [h.id for h in hosts]
    assert host.id in host_ids
    assert host2.id in host_ids

    # Remove a host
    uuids.pop(0)
    create_sp_event(client, agent_client, new_context,
                    sp_name,
                    sp_name, SP_CREATE, uuids, sp_name)
    hosts = wait_for(lambda: wait_host_count(storage_pool, 1))
    assert host2.id in hosts[0].id

    # Send empty host list
    uuids = []
    create_sp_event(client, agent_client, new_context,
                    sp_name,
                    sp_name, SP_CREATE, uuids, sp_name)
    hosts = wait_for(lambda: wait_host_count(storage_pool, 0))
    assert len(hosts) == 0


def test_ebs_volume_hard_affinity(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host.uuid, host2.uuid]
    host = super_client.update(host, labels={'io.rancher.host.zone': 'east'})
    super_client.wait_success(host)

    d1 = super_client.create_storageDriver(name=sp_name)
    d1 = super_client.wait_success(d1)
    d1 = super_client.update(d1, data={'fields':
                                       {'volumeAccessMode':
                                        'singleHostRW'}})
    d1 = super_client.wait_success(d1)
    assert d1.data["fields"]["volumeAccessMode"] == "singleHostRW"

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    storage_pool = super_client.update(storage_pool, storageDriverId=d1.id)
    storage_pool = super_client.wait_success(storage_pool)

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)
    v1 = super_client.update(v1, storageDriverId=d1.id,
                             driverOpts={'ec2_az': 'east'})
    v1 = client.wait_success(v1)

    launch_config = {"volumeDriver": sp_name,
                     "dataVolumes": [v1.name + ":/test"],
                     "imageUuid": new_context.image_uuid,
                     "stdinOpen": True
                     }
    scale = 1

    service, stack = create_env_and_svc(client, launch_config,
                                        scale)
    stack.activateservices()
    service = client.wait_success(service, 300)
    assert service.state == "active"
    container_list = get_service_container_list(super_client, service)
    container = container_list[0]
    key = 'io.rancher.scheduler.affinity:host_label'
    assert container.data.fields.labels[key] == 'io.rancher.host.zone=east'


def test_ebs_volume_hard_affinity_no_override(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host.uuid, host2.uuid]
    host = super_client.update(host, labels={'io.rancher.host.zone': 'east',
                                             'foo': 'bar'})
    super_client.wait_success(host)

    d1 = super_client.create_storageDriver(name=sp_name)
    d1 = super_client.wait_success(d1)
    d1 = super_client.update(d1, data={'fields':
                                       {'volumeAccessMode':
                                        'singleHostRW'}})
    d1 = super_client.wait_success(d1)
    assert d1.data["fields"]["volumeAccessMode"] == "singleHostRW"

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    storage_pool = super_client.update(storage_pool, storageDriverId=d1.id)
    storage_pool = super_client.wait_success(storage_pool)

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)
    v1 = super_client.update(v1, storageDriverId=d1.id,
                             driverOpts={'ec2_az': 'east'})
    v1 = client.wait_success(v1)

    labels = {'io.rancher.scheduler.affinity:host_label': 'foo=bar'}
    launch_config = {"volumeDriver": sp_name,
                     "dataVolumes": [v1.name + ":/test"],
                     "imageUuid": new_context.image_uuid,
                     "stdinOpen": True,
                     "labels": labels,
                     }
    scale = 1

    service, stack = create_env_and_svc(client, launch_config,
                                        scale)
    stack.activateservices()
    service = client.wait_success(service, 300)
    assert service.state == "active"
    container_list = get_service_container_list(super_client, service)
    container = container_list[0]
    key = 'io.rancher.scheduler.affinity:host_label'
    assert container.data.fields.labels[key] == \
        'foo=bar,io.rancher.host.zone=east'


def test_last_allocated_host_id(super_client, new_context):
    client, agent_client, host = from_context(new_context)
    sp_name = 'storage-%s' % random_str()
    host2 = register_simulated_host(new_context)
    host_uuids = [host2.uuid]

    d1 = super_client.create_storageDriver(name=sp_name)
    d1 = super_client.wait_success(d1)
    d1 = super_client.update(d1, data={'fields':
                                       {'volumeAccessMode':
                                        'singleHostRW'}})
    d1 = super_client.wait_success(d1)
    assert d1.data["fields"]["volumeAccessMode"] == "singleHostRW"

    create_sp_event(client, agent_client, new_context, sp_name, sp_name,
                    SP_CREATE, host_uuids, sp_name,
                    access_mode='singleHostRW')
    storage_pool = wait_for(lambda: sp_wait(client, sp_name))
    assert storage_pool.state == 'active'
    storage_pool = super_client.update(storage_pool, storageDriverId=d1.id)
    storage_pool = super_client.wait_success(storage_pool)

    assert storage_pool.volumeAccessMode == 'singleHostRW'

    # Create a volume with a driver that points to a storage pool
    v1 = client.create_volume(name=random_str(), driver=sp_name)
    v1 = client.wait_success(v1)

    launch_config = {"volumeDriver": sp_name,
                     "dataVolumes": [v1.name + ":/test"],
                     "imageUuid": new_context.image_uuid,
                     "stdinOpen": True
                     }
    scale = 1

    service, stack = create_env_and_svc(client, launch_config,
                                        scale)
    stack.activateservices()
    service = client.wait_success(service, 300)
    assert service.state == "active"
    v1 = super_client.update(v1)
    assert str(v1.data.lastAllocatedHostID) in host2.id


def create_volume_event(client, agent_client, context, event_type,
                        external_id, driver=None, uri=None):
    vol_event = {
        'externalId': external_id,
        'eventType': event_type,
        'volume': {
            'externalId': external_id,
            'name': external_id,
            'driver': driver,
            'uri': uri,
            'format': 'docker',
            'isHostPath': False,
        }
    }

    event = agent_client.create_external_volume_event(vol_event)
    assert event.externalId == external_id
    assert event.eventType == event_type
    event = wait_for(lambda: event_wait(client, event))
    assert event.accountId == context.project.id
    assert event.reportedAccountId == context.agent.id

    return event


def create_sp_event(client, agent_client, context, external_id, name,
                    event_type, host_uuids, driver_name, agent_account=None,
                    access_mode=None, block_device_path=None,
                    volume_capabilities=None, driver_id=None):
    storage_pool = {
        'name': name,
        'externalId': external_id,
        'driverName': driver_name,
    }

    if access_mode is not None:
        storage_pool['volumeAccessMode'] = access_mode

    if block_device_path is not None:
        storage_pool['blockDevicePath'] = block_device_path

    if volume_capabilities is not None:
        storage_pool['volumeCapabilities'] = volume_capabilities

    if driver_id is not None:
        storage_pool['storageDriverId'] = driver_id

    event = agent_client.create_external_storage_pool_event(
        externalId=external_id,
        eventType=event_type,
        hostUuids=host_uuids,
        storagePool=storage_pool)

    assert event.externalId == external_id
    assert event.eventType == event_type
    assert event.hostUuids == host_uuids

    event = wait_for(lambda: event_wait(client, event))
    assert event.accountId == context.project.id
    if agent_account:
        assert event.reportedAccountId == agent_account.id
    else:
        assert event.reportedAccountId == context.agent.id

    return event


def sp_wait(client, external_id):
    storage_pools = client.list_storage_pool(externalId=external_id)
    if len(storage_pools) and storage_pools[0].state == 'active':
        return storage_pools[0]


def volume_in_sp_by_name_wait(name, storage_pool):
    volumes = storage_pool.volumes(name=name)
    if len(volumes) and volumes[0].state == 'inactive':
        return volumes[0]


def volume_wait(client, external_id):
    volumes = client.list_volume(externalId=external_id)
    if len(volumes) and volumes[0].state == 'inactive':
        return volumes[0]


def wait_host_count(storage_pool, count):
    new_hosts = storage_pool.hosts()
    if len(new_hosts) == count:
        return new_hosts


def volume_in_sp(client, volume, storage_pool):
    volumes = storage_pool.volumes()
    if len(volumes) > 0:
        for v in volumes:
            if v.id == volume.id:
                return volume


def event_wait(client, event):
    created = client.by_id('externalEvent', event.id)
    if created is not None and created.state == 'created':
        return created


def create_env_and_svc(client, launch_config, scale=None, retainIp=False):
    env = create_env(client)
    service = create_svc(client, env, launch_config, scale, retainIp)
    return service, env


def create_env(client):
    random_name = random_str()
    env_name = random_name.replace("-", "")
    env = client.create_stack(name=env_name)
    env = client.wait_success(env)
    assert env.state == "active"
    return env


def create_svc(client, env, launch_config, scale=None, retainIp=False):
    random_name = random_str()
    service_name = random_name.replace("-", "")
    service = client.create_service(name=service_name,
                                    stackId=env.id,
                                    launchConfig=launch_config,
                                    scale=scale,
                                    retainIp=retainIp)

    service = client.wait_success(service)
    assert service.state == "inactive"
    return service


def get_service_container_list(admin_client, service, managed=None):
    container = []
    if managed is not None:
        all_instance_maps = \
            admin_client.list_serviceExposeMap(serviceId=service.id,
                                               managed=managed)
    else:
        all_instance_maps = \
            admin_client.list_serviceExposeMap(serviceId=service.id)

    instance_maps = []
    for instance_map in all_instance_maps:
        if instance_map.state not in ("removed", "removing"):
            instance_maps.append(instance_map)

    for instance_map in instance_maps:
        c = admin_client.by_id('container', instance_map.instanceId)
        assert c.state in CONTAINER_STATES
        containers = admin_client.list_container(
            externalId=c.externalId,
            include="hosts")
        assert len(containers) == 1
        container.append(containers[0])

    return container

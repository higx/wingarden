from common import *  # NOQA
from cattle import ApiError


def _create_stack(client):
    env = client.create_environment(name=random_str())
    env = client.wait_success(env)
    assert env.state == "active"
    return env


def test_activate_svc(client, context, super_client):
    env = _create_stack(client)

    image_uuid = context.image_uuid
    launch_config = {"imageUuid": image_uuid}
    scale_policy = {"min": 2, "max": 4, "increment": 2}

    svc = client.create_service(name=random_str(),
                                environmentId=env.id,
                                launchConfig=launch_config,
                                scalePolicy=scale_policy,
                                scale=6)
    svc = client.wait_success(svc)
    assert svc.state == "inactive"
    assert svc.scalePolicy is not None
    assert svc.scalePolicy.min == 2
    assert svc.scalePolicy.max == 4
    assert svc.scalePolicy.increment == 2

    client.wait_success(svc.activate())
    wait_for(lambda: super_client.reload(svc).currentScale >= 2)
    wait_for(lambda: client.reload(svc).healthState == 'healthy')


def test_activate_services_fail(super_client, new_context):
    client = new_context.client
    env = _create_stack(client)
    host = super_client.reload(register_simulated_host(new_context))
    wait_for(lambda: super_client.reload(host).state == 'active',
             timeout=5)

    image_uuid = new_context.image_uuid
    launch_config = {"imageUuid": image_uuid, 'ports': "5419"}
    scale_policy = {"min": 1, "max": 4}

    svc = client.create_service(name=random_str(),
                                environmentId=env.id,
                                launchConfig=launch_config,
                                scalePolicy=scale_policy)
    svc = client.wait_success(svc)
    assert svc.state == "inactive"

    # as we have only 2 hosts available,
    # service's final scale should be 2
    svc = wait_state(client, svc.activate(), 'active')
    wait_for(lambda: super_client.reload(svc).currentScale >= 1)
    wait_for(lambda: super_client.reload(svc).currentScale >= 1)


def test_scale_update(client, context, super_client):
    env = _create_stack(client)

    image_uuid = context.image_uuid
    launch_config = {"imageUuid": image_uuid}
    scale_policy = {"min": 1, "max": 3}

    svc = client.create_service(name=random_str(),
                                environmentId=env.id,
                                launchConfig=launch_config,
                                scalePolicy=scale_policy)
    svc = client.wait_success(svc)
    assert svc.state == "inactive"

    svc = client.wait_success(svc.activate())
    assert svc.state == "active"
    wait_for(lambda: super_client.reload(svc).currentScale >= 1)


def test_validate_scale_policy_create(client, context):
    env = _create_stack(client)

    image_uuid = context.image_uuid
    launch_config = {"imageUuid": image_uuid}

    scale_policy = {"min": 2, "max": 1, "increment": 2}
    with pytest.raises(ApiError) as e:
        client.create_service(name=random_str(),
                              environmentId=env.id,
                              launchConfig=launch_config,
                              scalePolicy=scale_policy)
    assert e.value.error.status == 422
    assert e.value.error.code == 'MaxLimitExceeded'


def test_validate_scale_policy_update(client, context):
    env = _create_stack(client)

    image_uuid = context.image_uuid
    launch_config = {"imageUuid": image_uuid}
    scale_policy = {"min": 1, "max": 4, "increment": 2}

    svc = client.create_service(name=random_str(),
                                environmentId=env.id,
                                launchConfig=launch_config,
                                scalePolicy=scale_policy)
    svc = client.wait_success(svc)
    assert svc.state == "inactive"

    # update with max scale < min scale
    scale_policy = {"max": 3, "min": 5,
                    "increment": 2}
    with pytest.raises(ApiError) as e:
        client.update(svc, scalePolicy=scale_policy)
    assert e.value.error.status == 422
    assert e.value.error.code == 'MaxLimitExceeded'


def test_policy_update(client, context, super_client):
    env = _create_stack(client)

    image_uuid = context.image_uuid
    launch_config = {"imageUuid": image_uuid}
    scale_policy = {"min": 1, "max": 4, "increment": 2}

    svc = client.create_service(name=random_str(),
                                environmentId=env.id,
                                launchConfig=launch_config,
                                scalePolicy=scale_policy)
    svc = client.wait_success(svc)
    assert svc.state == "inactive"

    svc.activate()
    svc = client.wait_success(svc, 120)
    wait_for(lambda: super_client.reload(svc).currentScale >= 1)

    # reduce the max
    scale_policy = {"min": 2, "max": 2,
                    "increment": 1}
    client.update(svc, scalePolicy=scale_policy)
    svc = client.wait_success(svc)
    wait_for(lambda: super_client.reload(svc).currentScale <= 2)

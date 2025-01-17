# vim: tabstop=4 shiftwidth=4 softtabstop=4

"""Main entry point into the EC2 Credentials service.

This service allows the creation of access/secret credentials used for
the ec2 interop layer of OpenStack.  

A user can create as many access/secret pairs, each of which map to a
specific tenant.  This is required because OpenStack supports a user
belonging to multiple tenants, whereas the signatures created on ec2-style
requests don't allow specification of which tenant the user wishs to act
upon.

To complete the cycle, we provide a method that OpenStack services can
use to validate a signature and get a corresponding openstack token.  This
token allows method calls to other services within the context the
access/secret was created.  As an example, nova requests keystone to validate
the signature of a request, receives a token, and then makes a request to glance
to list images needed to perform the requested task.

"""

import uuid

from keystone import catalog
from keystone import config
from keystone import identity
from keystone import policy
from keystone import token
from keystone.common import manager
from keystone.common import wsgi


CONF = config.CONF


class Manager(manager.Manager):
    """Default pivot point for the EC2 Credentials backend.

    See :mod:`keystone.manager.Manager` for more details on how this
    dynamically calls the backend.

    """

    def __init__(self):
        super(Manager, self).__init__(CONF.ec2.driver)


class Ec2Extension(wsgi.ExtensionRouter):
    def add_routes(self, mapper):
        ec2_controller = Ec2Controller()
        # validation
        mapper.connect('/ec2tokens',
                       controller=ec2_controller,
                       action='authenticate_ec2',
                       conditions=dict(method=['POST']))

        # crud
        mapper.connect('/users/{user_id}/credentials/OS-EC2',
                       controller=ec2_controller,
                       action='create_credential',
                       conditions=dict(method=['POST']))
        mapper.connect('/users/{user_id}/credentials/OS-EC2',
                       controller=ec2_controller,
                       action='get_credentials',
                       conditions=dict(method=['GET']))
        mapper.connect('/users/{user_id}/credentials/OS-EC2/{credential_id}',
                       controller=ec2_controller,
                       action='get_credential',
                       conditions=dict(method=['GET']))
        mapper.connect('/users/{user_id}/credentials/OS-EC2/{credential_id}',
                       controller=ec2_controller,
                       action='delete_credential',
                       conditions=dict(method=['DELETE']))


class Ec2Controller(wsgi.Application):
    def __init__(self):
        self.catalog_api = catalog.Manager()
        self.identity_api = identity.Manager()
        self.token_api = token.Manager()
        self.policy_api = policy.Manager()
        self.ec2_api = Manager()
        super(Ec2Controller, self).__init__()

    def authenticate_ec2(self, context, credentials=None,
                         ec2Credentials=None):
        """Validate a signed EC2 request and provide a token.

        Other services (such as Nova) use this **admin** call to determine
        if a request they signed received is from a valid user.

        If it is a valid signature, an openstack token that maps
        to the user/tenant is returned to the caller, along with
        all the other details returned from a normal token validation
        call.

        The returned token is useful for making calls to other 
        OpenStack services within the context of the request.

        :param context: standard context
        :param credentials: dict of ec2 signature
        :param ec2Credentials: DEPRECATED dict of ec2 signature
        :returns: token: openstack token equivalent to access key along
                         with the corresponding service catalog and roles
        """

        # FIXME(ja): validate that a service token was used!

        # NOTE(termie): backwards compat hack
        if not credentials and ec2Credentials:
            credentials = ec2Credentials
        creds_ref = self.ec2_api.get_credential(context,
                                                credentials['access'])

        signer = utils.Signer(creds_ref['secret'])
        signature = signer.generate(credentials)
        if signature == credentials['signature']:
            pass
        # NOTE(vish): Some libraries don't use the port when signing
        #             requests, so try again without port.
        elif ':' in credentials['signature']:
            hostname, _port = credentials['host'].split(":")
            credentials['host'] = hostname
            signature = signer.generate(credentials)
            if signature != credentials.signature:
                # TODO(termie): proper exception
                raise Exception("Not Authorized")
        else:
            raise Exception("Not Authorized")

        # TODO(termie): don't create new tokens every time
        # TODO(termie): this is copied from TokenController.authenticate
        token_id = uuid.uuid4().hex
        tenant_ref = self.identity_api.get_tenant(creds_ref['tenant_id'])
        user_ref = self.identity_api.get_user(creds_ref['user_id'])
        metadata_ref = self.identity_api.get_metadata(
                context=context,
                user_id=user_ref['id'],
                tenant_id=tenant_ref['id'])
        catalog_ref = self.catalog_api.get_catalog(
                context=context,
                user_id=user_ref['id'],
                tenant_id=tenant_ref['id'],
                    metadata=metadata_ref)

        token_ref = self.token_api.create_token(
                context, token_id, dict(expires='',
                                        id=token_id,
                                        user=user_ref,
                                        tenant=tenant_ref,
                                        metadata=metadata_ref))

        # TODO(termie): optimize this call at some point and put it into the
        #               the return for metadata
        # fill out the roles in the metadata
        roles_ref = []
        for role_id in metadata_ref.get('roles', []):
            roles_ref.append(self.identity_api.get_role(context, role_id))

        # TODO(termie): make this a util function or something
        # TODO(termie): i don't think the ec2 middleware currently expects a
        #               full return, but it contains a note saying that it
        #               would be better to expect a full return
        return TokenController._format_authenticate(
                self, token_ref, roles_ref, catalog_ref)

    def create_credential(self, context, user_id, tenant_id):
        """Create a secret/access pair for use with ec2 style auth.

        Generates a new set of credentials that map the the user/tenant
        pair.

        :param context: standard context
        :param user_id: id of user
        :param tenant_id: id of tenant
        :returns: credential: dict of ec2 credential
        """
        # TODO(termie): validate that this request is valid for given user
        #               tenant
        cred_ref = {'user_id': user_id,
                    'tenant_id': tenant_id,
                    'access': uuid.uuid4().hex,
                    'secret': uuid.uuid4().hex}
        self.ec2_api.create_credential(context, cred_ref['access'], cred_ref)
        return {'credential': cred_ref}

    def get_credentials(self, context, user_id):
        """List all credentials for a user.

        :param context: standard context
        :param user_id: id of user
        :returns: credentials: list of ec2 credential dicts
        """

        # TODO(termie): validate that this request is valid for given user
        #               tenant
        return {'credentials': self.ec2_api.list_credentials(context, user_id)}

    def get_credential(self, context, user_id, credential_id):
        """Retreive a user's access/secret pair by the access key.

        Grab the full access/secret pair for a given access key.

        :param context: standard context
        :param user_id: id of user
        :param credential_id: access key for credentials
        :returns: credential: dict of ec2 credential
        """
        # TODO(termie): validate that this request is valid for given user
        #               tenant
        return {'credential': self.ec2_api.get_credential(context,
                                                          credential_id)}

    def delete_credential(self, context, user_id, credential_id):
        """Delete a user's access/secret pair.

        Used to revoke a user's access/secret pair

        :param context: standard context
        :param user_id: id of user
        :param credential_id: access key for credentials
        :returns: bool: success
        """
        # TODO(termie): validate that this request is valid for given user
        #               tenant
        return self.ec2_api.delete_credential(context, credential_id)

# vim: tabstop=4 shiftwidth=4 softtabstop=4

"""Main entry point into the Catalog service."""

import uuid

import webob.exc

from keystone import config
from keystone.common import manager
from keystone.common import wsgi


CONF = config.CONF


class Manager(manager.Manager):
    """Default pivot point for the Catalog backend.

    See :mod:`keystone.manager.Manager` for more details on how this
    dynamically calls the backend.

    """

    def __init__(self):
        super(Manager, self).__init__(CONF.catalog.driver)


class ServiceController(wsgi.Application):
    def __init__(self):
        self.catalog_api = Manager()
        super(ServiceController, self).__init__()

    # CRUD extensions
    # NOTE(termie): this OS-KSADM stuff is not very consistent
    def get_services(self, context):
        service_list = self.catalog_api.list_services(context)
        service_refs = [self.catalog_api.get_service(context, x)
                        for x in service_list]
        return {'OS-KSADM:services': service_refs}

    def get_service(self, context, service_id):
        service_ref = self.catalog_api.get_service(context, service_id)
        if not service_ref:
            raise webob.exc.HTTPNotFound()
        return {'OS-KSADM:service': service_ref}

    def delete_service(self, context, service_id):
        service_ref = self.catalog_api.delete_service(context, service_id)

    def create_service(self, context, OS_KSADM_service):
        service_id = uuid.uuid4().hex
        service_ref = OS_KSADM_service.copy()
        service_ref['id'] = service_id
        new_service_ref = self.catalog_api.create_service(
                context, service_id, service_ref)
        return {'OS-KSADM:service': new_service_ref}

# vim: tabstop=4 shiftwidth=4 softtabstop=4

import os
import unittest
import subprocess
import sys
import time

from paste import deploy

from keystone import catalog
from keystone import config
from keystone import identity
from keystone import token
from keystone.common import logging
from keystone.common import utils
from keystone.common import wsgi


ROOTDIR = os.path.dirname(os.path.dirname(__file__))
VENDOR = os.path.join(ROOTDIR, 'vendor')
TESTSDIR = os.path.join(ROOTDIR, 'tests')
ETCDIR = os.path.join(ROOTDIR, 'etc')
CONF = config.CONF


cd = os.chdir


def rootdir(*p):
    return os.path.join(ROOTDIR, *p)


def etcdir(*p):
    return os.path.join(ETCDIR, *p)


def testsdir(*p):
    return os.path.join(TESTSDIR, *p)


def checkout_vendor(repo, rev):
    name = repo.split('/')[-1]
    if name.endswith('.git'):
        name = name[:-4]

    working_dir = os.getcwd()
    revdir = os.path.join(VENDOR, '%s-%s' % (name, rev.replace('/', '_')))
    modcheck = os.path.join(VENDOR, '.%s-%s' % (name, rev.replace('/', '_')))
    try:
        if os.path.exists(modcheck):
            mtime = os.stat(modcheck).st_mtime
            if int(time.time()) - mtime < 10000:
                return revdir

        if not os.path.exists(revdir):
            utils.git('clone', repo, revdir)

        cd(revdir)
        utils.git('pull')
        utils.git('checkout', '-q', rev)

        # write out a modified time
        with open(modcheck, 'w') as fd:
            fd.write('1')
    except subprocess.CalledProcessError as e:
        logging.warning('Failed to checkout %s', repo)
    cd(working_dir)
    return revdir


class TestClient(object):
    def __init__(self, app=None, token=None):
        self.app = app
        self.token = token

    def request(self, method, path, headers=None, body=None):
        if headers is None:
            headers = {}

        if self.token:
            headers.setdefault('X-Auth-Token', self.token)

        req = wsgi.Request.blank(path)
        req.method = method
        for k, v in headers.iteritems():
            req.headers[k] = v
        if body:
            req.body = body
        return req.get_response(self.app)

    def get(self, path, headers=None):
        return self.request('GET', path=path, headers=headers)

    def post(self, path, headers=None, body=None):
        return self.request('POST', path=path, headers=headers, body=body)

    def put(self, path, headers=None, body=None):
        return self.request('PUT', path=path, headers=headers, body=body)


class TestCase(unittest.TestCase):
    def __init__(self, *args, **kw):
        super(TestCase, self).__init__(*args, **kw)
        self._paths = []
        self._memo = {}

    def setUp(self):
        super(TestCase, self).setUp()

    def tearDown(self):
        for path in self._paths:
            if path in sys.path:
                sys.path.remove(path)
        CONF.reset()
        super(TestCase, self).tearDown()

    def load_backends(self):
        """Hacky shortcut to load the backends for data manipulation."""
        self.identity_api = utils.import_object(CONF.identity.driver)
        self.token_api = utils.import_object(CONF.token.driver)
        self.catalog_api = utils.import_object(CONF.catalog.driver)

    def load_fixtures(self, fixtures):
        """Hacky basic and naive fixture loading based on a python module.

        Expects that the various APIs into the various services are already
        defined on `self`.

        """
        # TODO(termie): doing something from json, probably based on Django's
        #               loaddata will be much preferred.
        for tenant in fixtures.TENANTS:
            rv = self.identity_api.create_tenant(tenant['id'], tenant)
            setattr(self, 'tenant_%s' % tenant['id'], rv)

        for user in fixtures.USERS:
            user_copy = user.copy()
            tenants = user_copy.pop('tenants')
            rv = self.identity_api.create_user(user['id'], user_copy)
            for tenant_id in tenants:
                self.identity_api.add_user_to_tenant(tenant_id, user['id'])
            setattr(self, 'user_%s' % user['id'], rv)

        for role in fixtures.ROLES:
            rv = self.identity_api.create_role(role['id'], role)
            setattr(self, 'role_%s' % role['id'], rv)

        for metadata in fixtures.METADATA:
            metadata_ref = metadata.copy()
            # TODO(termie): these will probably end up in the model anyway,
            #               so this may be futile
            del metadata_ref['user_id']
            del metadata_ref['tenant_id']
            rv = self.identity_api.create_metadata(metadata['user_id'],
                                                   metadata['tenant_id'],
                                                   metadata_ref)
            setattr(self,
                    'metadata_%s%s' % (metadata['user_id'],
                                       metadata['tenant_id']), rv)

    def _paste_config(self, config):
        if not config.startswith('config:'):
            test_path = os.path.join(TESTSDIR, config)
            etc_path = os.path.join(ROOTDIR, 'etc', config)
            for path in [test_path, etc_path]:
                if os.path.exists('%s.conf' % path):
                    return 'config:%s.conf' % path
        return config

    def loadapp(self, config, name='main'):
        return deploy.loadapp(self._paste_config(config), name=name)

    def appconfig(self, config):
        return deploy.appconfig(self._paste_config(config))

    def serveapp(self, config, name=None):
        app = self.loadapp(config, name=name)
        server = wsgi.Server(app, 0)
        server.start(key='socket')

        # Service catalog tests need to know the port we ran on.
        port = server.socket_info['socket'][1]
        CONF.public_port = port
        CONF.admin_port = port
        return server

    def client(self, app, *args, **kw):
        return TestClient(app, *args, **kw)

    def add_path(self, path):
        sys.path.insert(0, path)
        self._paths.append(path)

    def assertListEquals(self, expected, actual):
        copy = expected[:]
        #print expected, actual
        self.assertEquals(len(expected), len(actual))
        while copy:
            item = copy.pop()
            matched = False
            for x in actual:
                #print 'COMPARE', item, x,
                try:
                    self.assertDeepEquals(item, x)
                    matched = True
                    #print 'MATCHED'
                    break
                except AssertionError as e:
                    #print e
                    pass
            if not matched:
                raise AssertionError('Expected: %s\n Got: %s' % (expected,
                                                                 actual))

    def assertDictEquals(self, expected, actual):
        for k in expected:
            self.assertTrue(k in actual,
                            "Expected key %s not in %s." % (k, actual))
            self.assertDeepEquals(expected[k], actual[k])

        for k in actual:
            self.assertTrue(k in expected,
                            "Unexpected key %s in %s." % (k, actual))

    def assertDeepEquals(self, expected, actual):
        try:
            if type(expected) is type([]) or type(expected) is type(tuple()):
                # assert items equal, ignore order
                self.assertListEquals(expected, actual)
            elif type(expected) is type({}):
                self.assertDictEquals(expected, actual)
            else:
                self.assertEquals(expected, actual)
        except AssertionError as e:
            raise
            raise AssertionError('Expected: %s\n Got: %s' % (expected, actual))

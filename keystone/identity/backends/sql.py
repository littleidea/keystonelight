# vim: tabstop=4 shiftwidth=4 softtabstop=4

from keystone import identity
from keystone.common import sql
from keystone.common import utils
from keystone.common.sql import migration


class User(sql.ModelBase, sql.DictBase):
    __tablename__ = 'user'
    id = sql.Column(sql.String(64), primary_key=True)
    name = sql.Column(sql.String(64), unique=True)
    #password = sql.Column(sql.String(64))
    extra = sql.Column(sql.JsonBlob())

    @classmethod
    def from_dict(cls, user_dict):
        # shove any non-indexed properties into extra
        extra = {}
        for k, v in user_dict.copy().iteritems():
            # TODO(termie): infer this somehow
            if k not in ['id', 'name']:
                extra[k] = user_dict.pop(k)

        user_dict['extra'] = extra
        return cls(**user_dict)

    def to_dict(self):
        extra_copy = self.extra.copy()
        extra_copy['id'] = self.id
        extra_copy['name'] = self.name
        return extra_copy


class Tenant(sql.ModelBase, sql.DictBase):
    __tablename__ = 'tenant'
    id = sql.Column(sql.String(64), primary_key=True)
    name = sql.Column(sql.String(64), unique=True)
    extra = sql.Column(sql.JsonBlob())

    @classmethod
    def from_dict(cls, tenant_dict):
        # shove any non-indexed properties into extra
        extra = {}
        for k, v in tenant_dict.copy().iteritems():
            # TODO(termie): infer this somehow
            if k not in ['id', 'name']:
                extra[k] = tenant_dict.pop(k)

        tenant_dict['extra'] = extra
        return cls(**tenant_dict)

    def to_dict(self):
        extra_copy = self.extra.copy()
        extra_copy['id'] = self.id
        extra_copy['name'] = self.name
        return extra_copy


class Role(sql.ModelBase, sql.DictBase):
    __tablename__ = 'role'
    id = sql.Column(sql.String(64), primary_key=True)
    name = sql.Column(sql.String(64))


class Metadata(sql.ModelBase, sql.DictBase):
    __tablename__ = 'metadata'
    #__table_args__ = (
    #    sql.Index('idx_metadata_usertenant', 'user', 'tenant'),
    #    )

    user_id = sql.Column(sql.String(64), primary_key=True)
    tenant_id = sql.Column(sql.String(64), primary_key=True)
    data = sql.Column(sql.JsonBlob())


class UserTenantMembership(sql.ModelBase, sql.DictBase):
    """Tenant membership join table."""
    __tablename__ = 'user_tenant_membership'
    user_id = sql.Column(sql.String(64),
                         sql.ForeignKey('user.id'),
                         primary_key=True)
    tenant_id = sql.Column(sql.String(64),
                           sql.ForeignKey('tenant.id'),
                           primary_key=True)


class Identity(sql.Base, identity.Driver):
    # Internal interface to manage the database
    def db_sync(self):
        migration.db_sync()

    # Identity interface
    def authenticate(self, user_id=None, tenant_id=None, password=None):
        """Authenticate based on a user, tenant and password.

        Expects the user object to have a password field and the tenant to be
        in the list of tenants on the user.

        """
        user_ref = self._get_user(user_id)
        tenant_ref = None
        metadata_ref = None
        password = utils.hash_password(user_id, password)
        if not user_ref or user_ref['password'] != password:
            raise AssertionError('Invalid user / password')

        user_ref = self._filter_user_password(user_ref)

        tenants = self.get_tenants_for_user(user_id)
        if tenant_id and tenant_id not in tenants:
            raise AssertionError('Invalid tenant')

        tenant_ref = self.get_tenant(tenant_id)
        if tenant_ref:
            metadata_ref = self.get_metadata(user_id, tenant_id)
        else:
            metadata_ref = {}
        return user_ref, tenant_ref, metadata_ref

    def get_something(self, type, key, value):
        session = self.get_session()

        if key == 'name':
            ref = session.query(type).filter_by(name=value).first()
        elif key == 'id':
            ref = session.query(type).filter_by(id=value).first()

        if not ref:
            return
        return ref.to_dict()

    def get_tenant(self, tenant_id):
        return self.get_something(Tenant, 'id', tenant_id)

    def get_tenant_by_name(self, tenant_name):
        return self.get_something(Tenant, 'name', tenant_name)

    def _filter_user_password(self, user_ref):
        if not user_ref:
            return user_ref
        user_ref.pop('password', '')
        user_ref.pop('tenants', '')
        return user_ref

    def _get_user(self, user_id):
        return self.get_something(User, 'id', user_id)

    def get_user(self, user_id):
        return self._filter_user_password(self._get_user(user_id))

    def _get_user_by_name(self, user_name):
        return self.get_something(User, 'name', user_name)

    def get_user_by_name(self, user_name):
        return self._filter_user_password(self._get_user_by_name(user_name))

    def get_metadata(self, user_id, tenant_id):
        session = self.get_session()
        metadata_ref = session.query(Metadata)\
                              .filter_by(user_id=user_id)\
                              .filter_by(tenant_id=tenant_id)\
                              .first()
        return getattr(metadata_ref, 'data', None)

    def get_role(self, role_id):
        session = self.get_session()
        role_ref = session.query(Role).filter_by(id=role_id).first()
        return role_ref

    def list_users(self):
        session = self.get_session()
        user_refs = session.query(User)
        return [x.to_dict() for x in user_refs]

    def list_roles(self):
        session = self.get_session()
        role_refs = session.query(Role)
        return list(role_refs)

    # These should probably be part of the high-level API
    def add_user_to_tenant(self, tenant_id, user_id):
        session = self.get_session()
        q = session.query(UserTenantMembership)\
                   .filter_by(user_id=user_id)\
                   .filter_by(tenant_id=tenant_id)
        rv = q.first()
        if rv:
            return

        with session.begin():
            session.add(UserTenantMembership(user_id=user_id,
                                             tenant_id=tenant_id))
            session.flush()

    def remove_user_from_tenant(self, tenant_id, user_id):
        session = self.get_session()
        membership_ref = session.query(UserTenantMembership)\
                                .filter_by(user_id=user_id)\
                                .filter_by(tenant_id=tenant_id)\
                                .first()
        with session.begin():
            session.delete(membership_ref)
            session.flush()

    def get_tenants_for_user(self, user_id):
        session = self.get_session()
        membership_refs = session.query(UserTenantMembership)\
                          .filter_by(user_id=user_id)\
                          .all()

        return [x.tenant_id for x in membership_refs]

    def get_roles_for_user_and_tenant(self, user_id, tenant_id):
        metadata_ref = self.get_metadata(user_id, tenant_id)
        if not metadata_ref:
            metadata_ref = {}
        return metadata_ref.get('roles', [])

    def add_role_to_user_and_tenant(self, user_id, tenant_id, role_id):
        metadata_ref = self.get_metadata(user_id, tenant_id)
        is_new = False
        if not metadata_ref:
            is_new = True
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        roles.add(role_id)
        metadata_ref['roles'] = list(roles)
        if not is_new:
            self.update_metadata(user_id, tenant_id, metadata_ref)
        else:
            self.create_metadata(user_id, tenant_id, metadata_ref)

    def remove_role_from_user_and_tenant(self, user_id, tenant_id, role_id):
        metadata_ref = self.get_metadata(user_id, tenant_id)
        is_new = False
        if not metadata_ref:
            is_new = True
            metadata_ref = {}
        roles = set(metadata_ref.get('roles', []))
        roles.remove(role_id)
        metadata_ref['roles'] = list(roles)
        if not is_new:
            self.update_metadata(user_id, tenant_id, metadata_ref)
        else:
            self.create_metadata(user_id, tenant_id, metadata_ref)

    # CRUD
    def create_user(self, user_id, user):
        session = self.get_session()
        with session.begin():
            user_ref = User.from_dict(user)
            session.add(user_ref)
            session.flush()
        return user_ref.to_dict()

    def update_user(self, user_id, user):
        session = self.get_session()
        with session.begin():
            user_ref = session.query(User).filter_by(id=user_id).first()
            old_user_dict = user_ref.to_dict()
            for k in user:
                old_user_dict[k] = user[k]
            new_user = User.from_dict(old_user_dict)

            user_ref.name = new_user.name
            user_ref.extra = new_user.extra
            session.flush()
        return user_ref

    def delete_user(self, user_id):
        session = self.get_session()
        user_ref = session.query(User).filter_by(id=user_id).first()
        with session.begin():
            session.delete(user_ref)
            session.flush()

    def create_tenant(self, tenant_id, tenant):
        session = self.get_session()
        with session.begin():
            tenant_ref = Tenant.from_dict(tenant)
            session.add(tenant_ref)
            session.flush()
        return tenant_ref.to_dict()

    def update_tenant(self, tenant_id, tenant):
        session = self.get_session()
        with session.begin():
            tenant_ref = session.query(Tenant).filter_by(id=tenant_id).first()
            old_tenant_dict = tenant_ref.to_dict()
            for k in tenant:
                old_tenant_dict[k] = tenant[k]
            new_tenant = Tenant.from_dict(old_tenant_dict)

            tenant_ref.name = new_tenant.name
            tenant_ref.extra = new_tenant.extra
            session.flush()
        return tenant_ref

    def delete_tenant(self, tenant_id):
        session = self.get_session()
        tenant_ref = session.query(Tenant).filter_by(id=tenant_id).first()
        with session.begin():
            session.delete(tenant_ref)
            session.flush()

    def create_metadata(self, user_id, tenant_id, metadata):
        session = self.get_session()
        with session.begin():
            session.add(Metadata(user_id=user_id,
                                 tenant_id=tenant_id,
                                 data=metadata))
            session.flush()
        return metadata

    def update_metadata(self, user_id, tenant_id, metadata):
        session = self.get_session()
        with session.begin():
            metadata_ref = session.query(Metadata)\
                                  .filter_by(user_id=user_id)\
                                  .filter_by(tenant_id=tenant_id)\
                                  .first()
            data = metadata_ref.data.copy()
            for k in metadata:
                data[k] = metadata[k]
            metadata_ref.data = data
            session.flush()
        return metadata_ref

    def delete_metadata(self, user_id, tenant_id):
        self.db.delete('metadata-%s-%s' % (tenant_id, user_id))
        return None

    def create_role(self, role_id, role):
        session = self.get_session()
        with session.begin():
            session.add(Role(**role))
            session.flush()
        return role

    def update_role(self, role_id, role):
        session = self.get_session()
        with session.begin():
            role_ref = session.query(Role).filter_by(id=role_id).first()
            for k in role:
                role_ref[k] = role[k]
            session.flush()
        return role_ref

    def delete_role(self, role_id):
        session = self.get_session()
        role_ref = session.query(Role).filter_by(id=role_id).first()
        with session.begin():
            session.delete(role_ref)

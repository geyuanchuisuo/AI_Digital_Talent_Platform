"""B routes consume the shared server-derived account scope."""
from app.api.deps import CurrentUser
from app.core.auth.scope import get_account_scope, authorize_profile_access, DENY_MESSAGE
from app.core.errors import BizError


def require_manager(user: CurrentUser):
    scope = get_account_scope(user)
    if not scope.can_view_all_persons:
        raise BizError(403, DENY_MESSAGE)
    return scope


def require_profile_reader(talent_id: int, user: CurrentUser):
    scope = get_account_scope(user)
    if not authorize_profile_access(scope, talent_id).allowed:
        raise BizError(403, DENY_MESSAGE)
    return scope

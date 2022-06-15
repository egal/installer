import os

import requests
import yaml
import questionary
import subprocess
import random
import inflection
import pathlib
import re

from shutil import copyfile as copy_file
from os import remove as remove_file
from rich.console import Console
from sys import exit
from shutil import rmtree as remove_directory
from pathlib import Path

console = Console()
DOCKER_COMPOSE_VERSION = '3.7'
DOCKER_COMPOSE_FILE_NAME = 'docker-compose.yml'
DOCKER_COMPOSE_LOCAL_FILE_NAME = 'docker-compose.local.yml'
DOCKER_COMPOSE_DEPLOY_FILE_NAME = 'docker-compose.deploy.yml'
DOCKER_COMPOSE_DEPLOY_DEVELOP_FILE_NAME = 'docker-compose.deploy.develop.yml'
DOCKER_COMPOSE_DEPLOY_STAGE_FILE_NAME = 'docker-compose.deploy.stage.yml'
DOCKER_COMPOSE_DEPLOY_PRODUCTION_FILE_NAME = 'docker-compose.deploy.production.yml'
DOT_ENV_FILE_NAME = '.env'
DOT_ENV_EXAMPLE_FILE_NAME = '.env.example'
GITIGNORE_FILE_NAME = '.gitignore'
PLATFORM_REQUIREMENTS = ['git', 'docker', 'docker-compose']

user_services = {}
user_services_local = {}
databases = ['auth']
service_keys = []
dot_env = []
dot_env_example = []


def git(*args):
    try:
        subprocess.check_call(['git'] + list(args))
    except subprocess.CalledProcessError as e:
        if e.returncode == 128:
            print("""subprocess.CalleProcessError: Command returned non-zero exit status 128.
            Retrying clone process...
            """)
            print(args)
            git(args)
            exit()
        raise
    else:
        return True


def docker(*args):
    return subprocess.check_call(['docker'] + list(args))


def docker_compose_fn(*args):
    return subprocess.check_call(['docker-compose'] + list(args))


def generate_service_key():
    sample_string = 'abcdefghijklmnopqrstuvwxyz' + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' + '0123456789' + '@#%^*'
    return ''.join((random.choice(sample_string)) for x in range(32))


def check_platform_requirements(platform_requirements, need_exit=True):
    console.print('Checking platform requirements... [' + ','.join(map(str, platform_requirements)) + ']', style='bold')

    not_installed_requirements_count = 0
    null = open("/dev/null", "w")

    def check(requirement):
        try:
            subprocess.Popen(requirement, stdout=null, stderr=null)
            return True
        except OSError:
            console.print(f'`{requirement}` not found!', style='red bold')
            return False

    for platform_requirement in platform_requirements:
        if not check(platform_requirement):
            not_installed_requirements_count += 1

    null.close()

    if not_installed_requirements_count > 0:
        if need_exit:
            exit(1)
        else:
            return False

    console.print('Everyone platform requirements is present!', style='green bold')
    return True


def get_repo_latest_release_version(repo_name, replace_version_prefix=True):
    response = requests.get(f"https://api.github.com/repos/egal/{repo_name}/releases/latest")
    tag_name = response.json()['tag_name']

    if replace_version_prefix:
        return tag_name.replace('v', '', 1)

    return tag_name


def update_user_services(service_name, service_path, service_key_env_name):
    """Update dict user_services with new service"""
    user_services[service_name] = {
        'build': {'context': service_path},
        'restart': 'unless-stopped',
        'depends_on': ['rabbitmq', 'postgres'],
        'environment': {
            'APP_NAME': '${PROJECT_NAME}',
            'APP_SERVICE_NAME': get_shorten_service_name(service_name),
            'APP_SERVICE_KEY': '${' + service_key_env_name + '}',
            'DB_HOST': 'postgres',
            'DB_USERNAME': '${DB_USERNAME}',
            'DB_PASSWORD': '${DB_PASSWORD}',
            'RABBITMQ_HOST': 'rabbitmq',
            'RABBITMQ_USER': '${RABBITMQ_USER}',
            'RABBITMQ_PASSWORD': '${RABBITMQ_PASSWORD}',
            'WAIT_HOSTS': 'rabbitmq:5672,postgres:5432',
        },
    }


def update_user_services_local(service_name, service_path):
    """Update dict user_services_local with new service"""
    user_services_local[service_name] = {
        'build': {'args': {'DEBUG': 'true'}},
        'user': '${UID}:${GID}',
        'volumes': [f'./{service_path}:/app:rw'],
    }


def init_user_service_dir(service_path, git_repo_url):
    """Initialize directory containing source code for specific service"""
    git('clone', git_repo_url, service_path)
    remove_directory(f'{service_path}/.git')
    docker(
        'run',
        '--rm', '--interactive', '--tty',
        '--volume', f'{pathlib.Path().resolve()}/{service_path}:/app:rw',
        '--user', f'{os.getuid()}:{os.getgid()}',
        'composer', 'update',
        '--no-install', '--ignore-platform-reqs',
        '--no-interaction', '--no-progress',
        '--no-autoloader', '--no-cache'
    )


def init_auth_service():
    """Initialize auth-service directory based on build type selection.
    Returns dict type definition of auth-service
    """
    auth_service_type = questionary.select('What build type of auth-service you need?',
                                           choices=['Build from image', 'Build from context']).ask()
    auth_service_name = 'auth-service'

    if auth_service_type == 'Build from image':
        auth_service_definition = {
            'image': f"egalbox/auth-service:{get_repo_latest_release_version('auth-service')}",
        }
    elif auth_service_type == 'Build from context':
        auth_service_path = 'server/' + auth_service_name
        auth_service_definition = {
            'build': {'context': auth_service_path},
        }
        update_user_services_local(auth_service_name, auth_service_path)
        init_user_service_dir(auth_service_path, 'https://github.com/egal/auth-service.git')

    auth_service_definition.update({
        'restart': 'unless-stopped',
        'depends_on': ['rabbitmq', 'postgres'],
        'environment': {
            'APP_NAME': '${PROJECT_NAME}',
            'APP_SERVICE_NAME': 'auth',
            'APP_SERVICE_KEY': '${AUTH_SERVICE_KEY}',
            'APP_SERVICES': '${AUTH_SERVICE_ENVIRONMENT_APP_SERVICES}',
            'DB_HOST': 'postgres',
            'DB_USERNAME': '${DB_USERNAME}',
            'DB_PASSWORD': '${DB_PASSWORD}',
            'RABBITMQ_HOST': 'rabbitmq',
            'RABBITMQ_USER': '${RABBITMQ_USER}',
            'RABBITMQ_PASSWORD': '${RABBITMQ_PASSWORD}',
            'WAIT_HOSTS': 'rabbitmq:5672,postgres:5432',
        },
    })

    user_services[auth_service_name] = auth_service_definition
    console.print(f'Service `{auth_service_name}` added!', style='green bold')


def yml_beautify(file_name):
    """Add extra break before lines starting with `- `"""
    with open(file_name, 'r+') as f:
        file_data = f.read()
        file_data = re.sub('- ', '  - ', file_data, flags=re.MULTILINE)
        f.seek(0)
        f.write(file_data)
        f.truncate()
        f.close()


def get_shorten_service_name(service_name):
    """Get service_name without `-service` at the end"""
    return re.sub('-service', '', service_name)


def main():
    print("""

    ███████╗ ██████╗  █████╗ ██╗     
    ██╔════╝██╔════╝ ██╔══██╗██║     
    █████╗  ██║  ███╗███████║██║     
    ██╔══╝  ██║   ██║██╔══██║██║     
    ███████╗╚██████╔╝██║  ██║███████╗
    ╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝
              Installer

    """)

    check_platform_requirements(PLATFORM_REQUIREMENTS)

    # ------------------------------------- Checking dir is empty ------------------------------------- #

    initial_count = 0
    directory = '.'
    for path in os.listdir(directory):
        if os.path.isfile(os.path.join(directory, path)):
            initial_count += 1

    if initial_count > 1:
        console.print('Directory is not empty!', style='red bold')
        exit(1)

    # -------------------------------------------------------------------------- #

    console.print('Starting...', style='bold')

    project_name = questionary.text('Enter project name:').ask()

    client_type = questionary.select('What type of client you need?', choices=['Vue.js', 'Nuxt.js']).ask()

    if client_type == 'Vue.js':
        client_git_url = 'https://github.com/egal/vue-project.git'
    elif client_type == 'Nuxt.js':
        client_git_url = 'https://github.com/egal/nuxt-project.git'

    client_path = 'client'
    git('clone', client_git_url, client_path)
    remove_directory(f'{client_path}/.git')
    console.print('Client added!', style='green bold')

    init_auth_service()

    while questionary.confirm('Create new service?').ask():
        service_name = questionary.text('Enter service name, for example `core-service`:').ask()
        if service_name in user_services:
            console.print('This service name is already in use. Please choose another name.', style='red bold')
            continue

        service_key = generate_service_key()
        databases.append(service_name)
        service_keys.append(get_shorten_service_name(service_name) + ':' + service_key)
        service_path = f'server/{service_name}'
        service_key_env_name = inflection.underscore(service_name).upper() + '_KEY'
        dot_env.append(service_key_env_name + '=' + service_key)
        dot_env_example.append(service_key_env_name + '=')

        update_user_services(service_name, service_path, service_key_env_name)
        update_user_services_local(service_name, service_path)
        init_user_service_dir(service_path, 'https://github.com/egal/php-project.git')

        console.print(f'Service `{service_name}` added!', style='green bold')

    docker_compose = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {
            'postgres': {
                'image': 'egalbox/postgres:2.1.0',
                'restart': 'unless-stopped',
                'environment': {
                    'POSTGRES_USER': '${DB_USERNAME}',
                    'POSTGRES_PASSWORD': '${DB_PASSWORD}',
                    'POSTGRES_MULTIPLE_DATABASES': ','.join(map(str, databases)),
                },
            },
            'rabbitmq': {
                'image': f"egalbox/rabbitmq:{get_repo_latest_release_version('rabbitmq')}-management",
                'restart': 'unless-stopped',
                'environment': {
                    'RABBITMQ_USER': '${RABBITMQ_USER}',
                    'RABBITMQ_PASSWORD': '${RABBITMQ_PASSWORD}',
                },
            },
            'web-service': {
                'image': f"egalbox/web-service:{get_repo_latest_release_version('web-service')}",
                'restart': 'unless-stopped',
                'depends_on': ['rabbitmq'],
                'environment': {
                    'APP_NAME': '${PROJECT_NAME}',
                    'APP_SERVICE_NAME': 'web',
                    'RABBITMQ_HOST': 'rabbitmq',
                    'RABBITMQ_USER': '${RABBITMQ_USER}',
                    'RABBITMQ_PASSWORD': '${RABBITMQ_PASSWORD}',
                    'WAIT_HOSTS': 'rabbitmq:5672',
                },
            }
        },
    }

    for service_name in user_services:
        docker_compose['services'][service_name] = user_services[service_name]

    docker_compose_local = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {
            'postgres': {
                'ports': [{'published': 5432, 'target': 5432}]
            },
            'rabbitmq': {
                'ports': [{'published': 15672, 'target': 15672}, {'published': 5672, 'target': 5672}]
            },
            'web-service': {
                'ports': [{'published': 80, 'target': 8080}],
            },
        }
    }

    for service_name in user_services_local:
        docker_compose_local['services'][service_name] = user_services_local[service_name]

    docker_compose_deploy = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {
            'client': {
                'build': 'client',
                'restart': 'unless-stopped',
            },
        },
    }

    docker_compose_deploy_develop = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {}
    }

    docker_compose_deploy_stage = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {}
    }

    docker_compose_deploy_production = {
        'version': DOCKER_COMPOSE_VERSION,
        'services': {}
    }

    file = open(DOCKER_COMPOSE_DEPLOY_FILE_NAME, 'w+')
    yaml.dump(docker_compose_deploy, file, default_flow_style=False, sort_keys=False)
    file.close()

    file = open(DOCKER_COMPOSE_DEPLOY_DEVELOP_FILE_NAME, 'w+')
    yaml.dump(docker_compose_deploy_develop, file, default_flow_style=False, sort_keys=False)
    file.close()

    file = open(DOCKER_COMPOSE_DEPLOY_STAGE_FILE_NAME, 'w+')
    yaml.dump(docker_compose_deploy_stage, file, default_flow_style=False, sort_keys=False)
    file.close()

    file = open(DOCKER_COMPOSE_DEPLOY_PRODUCTION_FILE_NAME, 'w+')
    yaml.dump(docker_compose_deploy_production, file, default_flow_style=False, sort_keys=False)
    file.close()

    file = open(DOCKER_COMPOSE_FILE_NAME, 'w+')
    yaml.dump(docker_compose, file, default_flow_style=False, sort_keys=False)
    file.close()
    yml_beautify(DOCKER_COMPOSE_FILE_NAME)

    file = open(DOCKER_COMPOSE_LOCAL_FILE_NAME, 'w+')
    yaml.dump(docker_compose_local, file, default_flow_style=False, sort_keys=False)
    file.close()

    dot_env_example_file = open(DOT_ENV_EXAMPLE_FILE_NAME, 'w+')
    dot_env_example_file.write(f'PROJECT_NAME={project_name}\n')
    dot_env_example_file.write(f'COMPOSE_PROJECT_NAME={project_name}\n')
    dot_env_example_file.write(f'COMPOSE_FILE={DOCKER_COMPOSE_FILE_NAME}:{DOCKER_COMPOSE_LOCAL_FILE_NAME}\n')
    dot_env_example_file.write('RABBITMQ_USER=user\n')
    dot_env_example_file.write('DB_USERNAME=user\n')
    dot_env_example_file.close()

    copy_file(DOT_ENV_EXAMPLE_FILE_NAME, DOT_ENV_FILE_NAME)

    dot_env_example_file = open(DOT_ENV_EXAMPLE_FILE_NAME, 'a')
    dot_env_example_file.write('RABBITMQ_PASSWORD=\n')
    dot_env_example_file.write('DB_PASSWORD=\n')
    dot_env_example_file.write(f'AUTH_SERVICE_KEY=\n')
    dot_env_example_file.write('AUTH_SERVICE_ENVIRONMENT_APP_SERVICES=\n')
    dot_env_example_file.write('\n'.join(map(str, dot_env_example)) + '\n')
    dot_env_example_file.write('#UID=\n')
    dot_env_example_file.write('#GID=\n')
    dot_env_example_file.close()

    dot_env_file = open(DOT_ENV_FILE_NAME, 'a')
    dot_env_file.write('RABBITMQ_PASSWORD=password\n')
    dot_env_file.write('DB_PASSWORD=password\n')
    dot_env_file.write(f'AUTH_SERVICE_KEY={generate_service_key()}\n')
    dot_env_file.write('AUTH_SERVICE_ENVIRONMENT_APP_SERVICES=' + ','.join(map(str, service_keys)) + '\n')
    dot_env_file.write('\n'.join(map(str, dot_env)) + '\n')
    dot_env_file.write(f'UID={os.getuid()}\n')
    dot_env_file.write(f'GID={os.getgid()}\n')
    dot_env_file.close()

    dot_env_example_file.close()

    file = open(GITIGNORE_FILE_NAME, 'w')
    file.write('\n'.join(map(str, ['.env', '.idea', 'egal-installer*'])) + '\n')
    file.close()

    # ------------------------------------- server/proxy init ------------------------------------- #

    # TODO: server/proxy templates and configs.

    proxy_dir_path = 'server/proxy'
    Path(proxy_dir_path).mkdir(parents=True)
    testing_template_conf_file_name = 'testing.template.conf'
    proxy_development_template_conf = open(f'{proxy_dir_path}/{testing_template_conf_file_name}', 'w+')
    proxy_development_template_conf.write("""server {
        listen      80;
        server_name __SERVER_NAME__;

        location / {
            proxy_pass http://localhost:__CLIENT_PORT__;
        }

        location /api {
            rewrite ^/api(.*) /$1  break;
            proxy_pass http://localhost:__WEB_SERVICE_PORT__;
        }
    }
    """)
    proxy_development_template_conf.close()

    copy_file(f'{proxy_dir_path}/{testing_template_conf_file_name}', f'{proxy_dir_path}/development.template.conf')
    copy_file(f'{proxy_dir_path}/{testing_template_conf_file_name}', f'{proxy_dir_path}/staging.template.conf')
    copy_file(f'{proxy_dir_path}/{testing_template_conf_file_name}', f'{proxy_dir_path}/production.template.conf')

    # ------------------------------------- GitLab CI init ------------------------------------- #

    console.print('GitLab CI initialization...', style='bold')

    gitlab_ci_dir_path = '.gitlab-ci'
    # git('clone', 'https://github.com/egal/gitlab-ci.git', gitlab_ci_dir_path)
    remove_directory(f'{gitlab_ci_dir_path}/.git')
    remove_file(gitlab_ci_dir_path + '/.gitignore')
    remove_file(gitlab_ci_dir_path + '/LICENSE')
    copy_file(gitlab_ci_dir_path + '/stubs/.gitlab-ci.yml.stub', '.gitlab-ci.yml')

    pull_service_image_stub_file = open(gitlab_ci_dir_path + '/stubs/pull-service-image.yml.stub')
    pull_service_image_stub = pull_service_image_stub_file.read()
    pull_service_image_stub_file.close()

    build_service_image_stub_file = open(gitlab_ci_dir_path + '/stubs/build-service-image.yml.stub')
    build_service_image_stub = build_service_image_stub_file.read()
    build_service_image_stub_file.close()

    deploy_needs_build_stub_file = open(gitlab_ci_dir_path + '/stubs/deploy-needs-build.yml.stub')
    deploy_needs_build_stub = deploy_needs_build_stub_file.read()
    deploy_needs_build_stub_file.close()

    deploy_needs_pull_stub_file = open(gitlab_ci_dir_path + '/stubs/deploy-needs-pull.yml.stub')
    deploy_needs_pull_stub = deploy_needs_pull_stub_file.read()
    deploy_needs_pull_stub_file.close()

    migration_needs_build_stub_file = open(gitlab_ci_dir_path + '/stubs/migration-needs-build.yml.stub')
    migration_needs_build_stub = migration_needs_build_stub_file.read()
    migration_needs_build_stub_file.close()

    migration_needs_pull_stub_file = open(gitlab_ci_dir_path + '/stubs/migration-needs-pull.yml.stub')
    migration_needs_pull_stub = migration_needs_pull_stub_file.read()
    migration_needs_pull_stub_file.close()

    phpcs_stub_file = open(gitlab_ci_dir_path + '/stubs/phpcs.yml.stub')
    phpcs_stub = phpcs_stub_file.read()
    phpcs_stub_file.close()

    phpunit_stub_file = open(gitlab_ci_dir_path + '/stubs/phpunit.yml.stub')
    phpunit_stub = phpunit_stub_file.read()
    phpunit_stub_file.close()

    deploy_file = open('.gitlab-ci/deploy.gitlab-ci.yml', mode='a')
    testing_file = open('.gitlab-ci/testing.deploy.gitlab-ci.yml', mode='a')

    for service_name in user_services:
        if 'build' in user_services[service_name]:
            service_build = build_service_image_stub.replace('__SERVICE_NAME__', service_name)
            service_deploy = deploy_needs_build_stub.replace('__SERVICE_NAME__', service_name)
            service_migration = migration_needs_build_stub.replace('__SERVICE_NAME__', service_name)
            service_phpcs = phpcs_stub.replace('__SERVICE_NAME__', service_name)
            service_phpunit = phpunit_stub.replace('__SERVICE_NAME__', service_name)
            deploy_file.write("\n" + service_build)
            deploy_file.write("\n" + service_migration)
            deploy_file.write("\n" + service_deploy)
            testing_file.write("\n" + service_phpcs)
            testing_file.write("\n" + service_phpunit)
        elif 'image' in user_services[service_name]:
            service_pull = pull_service_image_stub.replace('__SERVICE_NAME__', service_name)
            service_deploy = deploy_needs_pull_stub.replace('__SERVICE_NAME__', service_name)
            service_migration = migration_needs_pull_stub.replace('__SERVICE_NAME__', service_name)
            deploy_file.write("\n" + service_pull)
            deploy_file.write("\n" + service_migration)
            deploy_file.write("\n" + service_deploy)

    deploy_file.close()
    testing_file.close()

    remove_directory(f'{gitlab_ci_dir_path}/stubs')

    # ------------------------------------- Composer installing ------------------------------------- #

    console.print('Composer installing...', style='bold')

    for service_name in user_services:
        docker(
            'run',
            '--rm', '--interactive', '--tty',
            '--volume', f'{pathlib.Path().resolve()}/{service_path}:/app:rw',
            '--user', f'{os.getuid()}:{os.getgid()}',
            'composer', 'install',
            '--ignore-platform-reqs', '--no-cache',
            '--no-interaction', '--no-progress'
        )

    # ------------------------------------- Completed ------------------------------------- #

    console.print('Completed!', style='green bold')


if __name__ == '__main__':
    main()
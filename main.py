import argparse
import questionary
import yaml
from shutil import copyfile as file_copy
from rich.console import Console
import subprocess
from sys import exit
import random
import string
from shutil import rmtree as rm_dir
import inflection
import nginx
import pathlib

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


def git(*args):
    return subprocess.check_call(['git'] + list(args))


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


def main():
    check_platform_requirements(PLATFORM_REQUIREMENTS)
    console.print('Starting...', style='bold')

    project_name = questionary.text('Inter project name:').ask()

    user_services = {}
    user_services_local = {}
    databases = ['auth']
    service_keys = []
    dot_env = []
    dot_env_example = []

    client_type = questionary.select('What type of client you need?', choices=['Vue.js', 'Nuxt.js']).ask()

    if client_type == 'Vue.js':
        client_git_url = 'git@github.com:egal/vue-project.git'
    elif client_type == 'Nuxt.js':
        client_git_url = 'git@github.com:egal/nuxt-project.git'

    client_path = 'client'
    git('clone', client_git_url, client_path)
    rm_dir(f'{client_path}/.git')
    console.print('Client added!', style='green bold')

    while questionary.confirm('Create new service?').ask():
        service_name = questionary.text('Inter service name:').ask()
        service_int_name = service_name + '-service'
        service_key = generate_service_key()
        databases.append(service_name)
        service_keys.append(service_name + ':' + service_key)
        service_path = f'server/{service_int_name}'
        service_key_env_name = inflection.underscore(service_int_name).upper() + '_KEY'
        dot_env.append(service_key_env_name + '=' + service_key)
        dot_env_example.append(service_key_env_name + '=')
        user_services[service_int_name] = {
            'build': {'context': service_path},
            'restart': 'unless-stopped',
            'depends_on': ['rabbitmq', 'postgres'],
            'environment': {
                'APP_NAME': '${PROJECT_NAME}',
                'APP_SERVICE_NAME': f'{service_name}',
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
        user_services_local[service_int_name] = {
            'build': {'args': {'DEBUG': 'true'}},
            'volumes': [f'./{service_path}:/app:rw'],
        }
        git('clone', 'git@github.com:egal/php-project.git', service_path)
        rm_dir(f'{service_path}/.git')
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
                'image': 'egalbox/rabbitmq:2.0.0-beta.1',  # TODO: Сменить на стабильную версию.
                'restart': 'unless-stopped',
                'environment': {
                    'RABBITMQ_USER': '${RABBITMQ_USER}',
                    'RABBITMQ_PASSWORD': '${RABBITMQ_PASSWORD}',
                },
            },
            'web-service': {
                'image': 'egalbox/web-service:2.0.0beta34',  # TODO: Сменить на стабильную версию.
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
            },
            'auth-service': {
                'image': 'egalbox/auth-service:2.0.0beta40',
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
            },
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
            'proxy': {
                'build': 'server/proxy',
                'restart': 'unless-stopped',
                'depends_on': ['client', 'web-service'],
                'environment': {
                    'WAIT_HOSTS': 'web-service:8080,client:80',
                },
                'ports': [{'published': 80, 'target': 80}, {'published': 443, 'target': 443}]
            },
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

    pathlib.Path('server/proxy').mkdir(parents=True, exist_ok=True)

    file = open('server/proxy/Dockerfile', 'w+')
    file.write('\n'.join(map(str, [
        'FROM nginx:1.19.6-alpine',
        'ADD https://github.com/ufoscout/docker-compose-wait/releases/download/2.8.0/wait /wait',
        'RUN chmod +x /wait',
        'COPY .conf /etc/nginx/conf.d/default.conf',
        'CMD /bin/sh -c "/wait && nginx -g \'daemon off;\'"',
    ])) + '\n')
    file.close()

    nginx_conf = nginx.Conf()
    nginx_server_conf = nginx.Server()
    nginx_server_conf.add(
        nginx.Key('listen', '*:80'),
        nginx.Location(
            '/',
            nginx.Key('proxy_pass', 'http://client:8080')
        ),
        nginx.Location(
            '/api',
            nginx.Key('rewrite', '^/api(.*) /$1  break'),
            nginx.Key('proxy_pass', 'http://web-service:8080')
        )
    )
    nginx_conf.add(nginx_server_conf)

    nginx.dumpf(nginx_conf, 'server/proxy/.conf')

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

    file = open(DOCKER_COMPOSE_LOCAL_FILE_NAME, 'w+')
    yaml.dump(docker_compose_local, file, default_flow_style=False, sort_keys=False)
    file.close()

    dot_env_example_file = open(DOT_ENV_EXAMPLE_FILE_NAME, 'w+')
    dot_env_example_file.write(f'PROJECT_NAME={project_name}\n')
    dot_env_example_file.write(f'COMPOSE_FILE={DOCKER_COMPOSE_FILE_NAME}:{DOCKER_COMPOSE_LOCAL_FILE_NAME}\n')
    dot_env_example_file.write('RABBITMQ_USER=user\n')
    dot_env_example_file.write('DB_USERNAME=user\n')
    dot_env_example_file.close()

    file_copy(DOT_ENV_EXAMPLE_FILE_NAME, DOT_ENV_FILE_NAME)

    dot_env_example_file = open(DOT_ENV_EXAMPLE_FILE_NAME, 'a')
    dot_env_example_file.write('RABBITMQ_PASSWORD=\n')
    dot_env_example_file.write('DB_PASSWORD=\n')
    dot_env_example_file.write(f'AUTH_SERVICE_KEY=\n')
    dot_env_example_file.write('AUTH_SERVICE_ENVIRONMENT_APP_SERVICES=\n')
    dot_env_example_file.write('\n'.join(map(str, dot_env_example)) + '\n')
    dot_env_example_file.close()

    dot_env_file = open(DOT_ENV_FILE_NAME, 'a')
    dot_env_file.write('RABBITMQ_PASSWORD=password\n')
    dot_env_file.write('DB_PASSWORD=password\n')
    dot_env_file.write(f'AUTH_SERVICE_KEY={generate_service_key()}\n')
    dot_env_file.write('AUTH_SERVICE_ENVIRONMENT_APP_SERVICES=' + ','.join(map(str, service_keys)) + '\n')
    dot_env_file.write('\n'.join(map(str, dot_env)) + '\n')
    dot_env_file.close()

    dot_env_example_file.close()

    file = open(GITIGNORE_FILE_NAME, 'w')
    file.write('\n'.join(map(str, ['.env', '.idea'])) + '\n')
    file.close()

    console.print('Done!', style='green bold')


if __name__ == '__main__':
    main()

# TODO: Билд скрипта с прикреплением к релизу
# TODO: Подгрузка .gitlab-ci

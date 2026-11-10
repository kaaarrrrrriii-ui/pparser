<?php

namespace Espo\Custom\Classes\ConsoleCommands;

use Espo\Core\Console\Command;
use Espo\Core\Console\Command\Params;
use Espo\Core\Console\IO;
use Espo\Core\Field\LinkMultiple;
use Espo\Core\Utils\Config\ConfigWriter;
use Espo\Entities\Role;
use Espo\Entities\User;
use Espo\ORM\EntityManager;
use RuntimeException;

/** Idempotent setup of the lead workspace and a restricted integration API user. */
class LeadgenSetup implements Command
{
    public function __construct(private EntityManager $entityManager, private ConfigWriter $configWriter)
    {}

    public function run(Params $params, IO $io): void
    {
        $key = getenv('ESPO_API_KEY');
        if (!$key || strlen($key) < 32) {
            throw new RuntimeException('ESPO_API_KEY must contain at least 32 characters.');
        }
        $panelUrl = getenv('LEADGEN_PUBLIC_URL') ?: 'http://localhost:8090';
        $this->configWriter->setMultiple([
            'applicationName' => 'Лиды · EspoCRM',
            'language' => 'ru_RU',
            // Calls are made from Orenburg (UTC+5); dates are stored in UTC either way.
            'timeZone' => 'Asia/Yekaterinburg',
            'tabList' => ['Lead', 'Task', (object) [
                'type' => 'url', 'text' => 'Сбор компаний', 'url' => $panelUrl,
                'iconClass' => 'fas fa-map-marked-alt', 'openInNewTab' => true,
            ]],
            'quickCreateList' => ['Lead', 'Task'],
            'globalSearchEntityList' => ['Lead'],
            'activitiesEntityList' => ['Task'],
            'historyEntityList' => ['Task'],
            'massEmailDisabled' => true,
        ]);
        $this->configWriter->save();

        $roles = $this->entityManager->getRDBRepositoryByClass(Role::class);
        $role = $roles->where(['name' => 'Leadgen API'])->findOne() ?? $roles->getNew();
        $role->set('name', 'Leadgen API');
        $role->setRawData([
            'Lead' => ['create' => 'yes', 'read' => 'all', 'edit' => 'all', 'delete' => 'no', 'stream' => 'no'],
            'Account' => false, 'Contact' => false, 'Opportunity' => false,
            'Campaign' => false, 'Case' => false, 'Task' => false,
        ]);
        $this->entityManager->saveEntity($role);

        $users = $this->entityManager->getRDBRepositoryByClass(User::class);
        $user = $users->where(['userName' => 'leadgen-integration'])->findOne();
        if ($user && !$user->isApi()) {
            throw new RuntimeException('The integration username is occupied by a non-API user.');
        }
        $user ??= $users->getNew();
        $user->set([
            'userName' => 'leadgen-integration', 'lastName' => 'Map collectors',
            'type' => 'api', 'isActive' => true, 'authMethod' => 'ApiKey', 'apiKey' => $key,
        ]);
        $user->setRoles(LinkMultiple::create()->withAddedIdList([$role->getId()]));
        $this->entityManager->saveEntity($user);
        $this->setupNotifier($io);
        $io->writeLine('Leadgen workspace configured. API key was not printed.');
    }

    /** Telegram reminders: reads leads, reads and updates callback tasks, deletes nothing. */
    private function setupNotifier(IO $io): void
    {
        $key = getenv('NOTIFIER_API_KEY');
        if (!$key) {
            $io->writeLine('NOTIFIER_API_KEY is not set; Telegram notifier user skipped.');
            return;
        }
        if (strlen($key) < 32) {
            throw new RuntimeException('NOTIFIER_API_KEY must contain at least 32 characters.');
        }
        $roles = $this->entityManager->getRDBRepositoryByClass(Role::class);
        $role = $roles->where(['name' => 'Telegram notifier'])->findOne() ?? $roles->getNew();
        $role->set('name', 'Telegram notifier');
        $role->setRawData([
            'Lead' => ['create' => 'no', 'read' => 'all', 'edit' => 'no', 'delete' => 'no', 'stream' => 'no'],
            'Task' => ['create' => 'yes', 'read' => 'all', 'edit' => 'all', 'delete' => 'no', 'stream' => 'no'],
            'Account' => false, 'Contact' => false, 'Opportunity' => false, 'Campaign' => false, 'Case' => false,
        ]);
        $this->entityManager->saveEntity($role);
        $users = $this->entityManager->getRDBRepositoryByClass(User::class);
        $user = $users->where(['userName' => 'telegram-notifier'])->findOne();
        if ($user && !$user->isApi()) {
            throw new RuntimeException('The notifier username is occupied by a non-API user.');
        }
        $user ??= $users->getNew();
        $user->set([
            'userName' => 'telegram-notifier', 'lastName' => 'Telegram notifier',
            'type' => 'api', 'isActive' => true, 'authMethod' => 'ApiKey', 'apiKey' => $key,
        ]);
        $user->setRoles(LinkMultiple::create()->withAddedIdList([$role->getId()]));
        $this->entityManager->saveEntity($user);
    }
}

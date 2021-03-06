# -*- encoding: utf-8 -*-
"""A module that provides utilities to test entities via genetic algorithms"""
import random
import yaml
import attr
from pathlib import Path
from logzero import logger
from rizza import entity_tester
from rizza.helpers import genetics
from rizza.helpers.misc import dict_search


@attr.s()
class GeneticEntityTester():
    """Class that handles all aspects of genetic algorithm-based testing

    :param config: Required. A config class instance.
    :param entity: A string name of the target entity.
    :param method: A string name of the entity's target method.
    :param population_count: Integer specifying the number in each generation.
    :param max_generations: Integer specifying the max number of generations.
    :param seek_bad: Boolean noting whether to favor bad results.
    :param fresh: Boolean noting whether to use the last best saved result.

    """

    config = attr.ib()
    entity = attr.ib(validator=attr.validators.instance_of(str))
    method = attr.ib(validator=attr.validators.instance_of(str))
    population_count = attr.ib(default=None)
    max_generations = attr.ib(default=None)
    max_recursive_generations = attr.ib(default=None)
    max_recursive_depth = attr.ib(default=None)
    disable_dependencies = attr.ib(default=None)
    disable_recursion = attr.ib(default=None)
    seek_bad = attr.ib(default=False)
    fresh = attr.ib(default=False)

    def __attrs_post_init__(self):
        """Perform more complex class initialzation"""
        # Entity method positive/negative
        self.test_name = '{} {} {}'.format(
            self.entity, self.method,
            'negative' if self.seek_bad else 'positive'
        )
        # If for some reason, the genetic config wasn't populated
        if not self.config.RIZZA.get('GENETICS', None):
            # try to load it again
            self.config._load_genetics()
        if not self.population_count:
            self.population_count = (
                self.config.RIZZA['GENETICS']['POPULATION COUNT'])
        if not self.max_generations:
            self.max_generations = (
                self.config.RIZZA['GENETICS']['MAX GENERATIONS'])

        # cli overrides
        if self.max_recursive_generations:
            self.config.RIZZA['GENETICS'][
                'MAX RECURSIVE GENERATIONS'] = self.max_recursive_generations
        if self.disable_dependencies:
            self.config.RIZZA['GENETICS']['ALLOW DEPENDENCIES'] = False
        if self.disable_recursion:
            self.config.RIZZA['GENETICS']['ALLOW RECURSION'] = False
        if self.max_recursive_depth:
            self.config.RIZZA['GENETICS'][
                'MAX RECURSIVE DEPTH'] = self.max_recursive_depth

        self._entity_inst = entity_tester.EntityTester.pull_entities()[self.entity]
        meths = entity_tester.EntityTester.pull_methods(self._entity_inst)
        if meths:
            self._method_inst = meths.get(self.method)
        else:
            self._method_inst = None
        self._etester = entity_tester.EntityTester(self.entity)
        self._etester.prep()

    def _save_test(self, test):
        """Save the test to the appropriate file in data/genetic_tests"""
        test_file = Path('data', 'genetic_tests', '{}.yaml'.format(self.entity))
        tests = yaml.load(test_file.open('r+')) or {}
        tests[self.test_name] = test
        yaml.dump(tests, test_file.open('w+'), default_flow_style=False)

    def _load_test(self):
        """Load in the last test stored in data/genetic_tests, if any exist"""
        test_file = Path('data', 'genetic_tests', '{}.yaml'.format(self.entity))
        if test_file.exists():
            tests = yaml.load(test_file.open('r')) or {}
            best = tests.get(self.test_name, False)
            if best:
                # convert the yaml format to a gene list
                fields, field_inputs = ([], [])
                for field, inpt in best['field_dict'].items():
                    fields.append(field)
                    field_inputs.append(inpt)
                args, arg_inputs = ([], [])
                for arg, inpt in best['arg_dict'].items():
                    args.append(arg)
                    arg_inputs.append(inpt)
                return [fields, field_inputs, args, arg_inputs]
        else:
            test_file.touch()
        return False

    def _judge(self, result=None, mock=False):
        """Return a numeric value for the given result"""
        if mock:  # Used for testing the class without true execution
            return random.randint(-1000,1000)
        total = 0
        for criteria, points in self.config.RIZZA['GENETICS']['CRITERIA'].items():
            if dict_search(criteria, result):
                total += points
        return total

    def _genes_to_task(self, genes):
        """Turn a gene list into an Entity Test Task"""
        field_dict = {field: inpt for (field, inpt) in zip(genes[0], genes[1])}
        arg_dict = {arg: inpt for (arg, inpt) in zip(genes[2], genes[3])}
        return entity_tester.EntityTestTask(
            entity=self.entity,
            method=self.method,
            field_dict=field_dict,
            arg_dict=arg_dict,
            config=self.config
        )

    def _create_gene_base(self):
        """Create a valid genetic base to evolve on"""
        # create a list of fields
        fields = [
            random.choice(list(self._etester.fields))
            for _ in range(random.randint(0, len(list(self._etester.fields))))
        ]
        # match random inputs to the previous list of fields
        inputs = list(entity_tester.EntityTester.pull_input_methods())
        field_inputs = [random.choice(inputs) for _ in range(len(fields))]
        # create a list of random method inputs
        args = entity_tester.EntityTester.pull_args(self._method_inst)
        args = [random.choice(args) for _ in range(random.randint(0, len(args)))]
        # match random inputs to the previous list of args
        arg_inputs = [random.choice(inputs) for _ in range(len(args))]
        return [fields, field_inputs, args, arg_inputs]

    def run(self, mock=False, save_only_passed=False):
        """Run a population attempting to maximize desired results"""
        if not self._method_inst:
            return None

        # Create our population
        population = genetics.Population(
            gene_base=[self._create_gene_base()],
            population_count=self.population_count,
            generator_function=self._create_gene_base,
            gene_length=1,
            mutate=False,
            rev_pop_sort=not self.seek_bad
        )
        # Attempt to continue where we left off, if desired
        if not self.fresh:
            best = self._load_test()
            if best:
                population.population[0].genes = best

        for generation in range(self.max_generations):
            for organism in population.population:
                logger.debug('Testing {}'.format(organism))
                # create an entity_tester. from the organism
                task = self._genes_to_task(organism.genes)
                # execute the test task
                result = task.execute(mock)
                if 'pass' in result and not mock and not self.seek_bad:
                    self._save_test(attr.asdict(
                        self._genes_to_task(organism.genes),
                        filter=lambda attr, value: attr.name != 'config'))
                    logger.info('Success! Generation {} passed with:\n{}'.format(
                        generation,
                        yaml.dump(
                            attr.asdict(
                                self._genes_to_task(organism.genes),
                                filter=lambda attr, value: attr.name != 'config'
                            ),
                            default_flow_style=False)
                    ))
                    return True
                # judge the results and pass those points to the organism
                organism.points = self._judge(result, mock)

            population.sort_population()
            logger.info('Generation {} best: {}'.format(
                generation, population.population[0]))
            # breed the current generation and iterate
            population.breed_population()
        if not mock and not save_only_passed:
            # save the current best in the config
            self._save_test(attr.asdict(
                self._genes_to_task(population.population[0].genes),
                filter=lambda attr, value: attr.name != 'config'))

    def run_best(self):
        """Pull the best saved test, if any, run it, and return the id"""
        self.config.RIZZA['GENETICS']['ALLOW RECURSION'] = False
        self.config.RIZZA['GENETICS']['MAX GENERATIONS'] = 1
        test = self._load_test()
        if test:
            test = self._genes_to_task(test)
            logger.info('Creating {}...'.format(self.entity))
            result = test.execute()
            if 'pass' in result:
                return result['pass'].get('id', -1)
        return -1

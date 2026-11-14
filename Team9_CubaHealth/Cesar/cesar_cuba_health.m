
clear; clc; close all;

%% ---------- Slide 2: Services offered at each clinic ----------
fid = fopen('cuba_clinics.csv', 'r');
header = fgetl(fid);
C = textscan(fid, '%s %s %s %f %f %f %f %f %f %f %s', 'Delimiter', ',');
fclose(fid);

facility = C{1};
distance = C{3};
services = [C{4} C{5} C{6} C{7} C{8} C{9} C{10}];
serviceNames = {'Primary care', 'Dental', 'Behavioral health', ...
                'Pharmacy', 'Optometry', 'Emergency', 'Inpatient'};

% Label each row with the facility and its distance from Cuba
rowLabels = cell(numel(facility), 1);
for k = 1:numel(facility)
    rowLabels{k} = sprintf('%s (%s)', facility{k}, distance{k});
end

figure('Position', [100 100 1100 450]);
imagesc(services);
colormap([0.93 0.93 0.93; 0.16 0.45 0.70]);   % grey = no, blue = yes
caxis([0 1]);
set(gca, 'XTick', 1:numel(serviceNames), 'XTickLabel', serviceNames, ...
         'YTick', 1:numel(rowLabels), 'YTickLabel', rowLabels, ...
         'FontSize', 11, 'TickLength', [0 0]);
hold on;
% grid lines between cells
for x = 0.5:1:numel(serviceNames) + 0.5
    plot([x x], [0.5 numel(facility) + 0.5], 'w-', 'LineWidth', 2);
end
for y = 0.5:1:numel(facility) + 0.5
    plot([0.5 numel(serviceNames) + 0.5], [y y], 'w-', 'LineWidth', 2);
end
title('Services offered by clinics serving Cuba, NM (blue = offered)', 'FontSize', 13);
print('fig_clinic_services.png', '-dpng', '-r200');

% Count how many local (in-town) options offer each service
inTown = strcmp(distance, 'In town');
fprintf('Services available in Cuba itself:\n');
for s = 1:numel(serviceNames)
    fprintf('  %-18s %d of %d in-town clinics\n', serviceNames{s}, ...
            sum(services(inTown, s)), sum(inTown));
end

%% ---------- Slide 4a: Age distribution ----------
fid = fopen('cuba_age_distribution.csv', 'r');
header = fgetl(fid);
A = textscan(fid, '%s %f %f', 'Delimiter', ',');
fclose(fid);

ageGroup = A{1};
ageCount = A{2};
agePct   = A{3};

figure('Position', [100 100 800 450]);
b = bar(agePct, 0.6);
set(b, 'FaceColor', [0.16 0.45 0.70]);
set(gca, 'XTick', 1:numel(ageGroup), 'XTickLabel', ageGroup, 'FontSize', 11);
ylabel('Percent of residents');
ylim([0 max(agePct) + 6]);
for k = 1:numel(agePct)
    text(k, agePct(k) + 1, sprintf('%.1f%% (%d)', agePct(k), ageCount(k)), ...
         'HorizontalAlignment', 'center', 'FontSize', 10);
end
title(sprintf('Cuba, NM population by age (total %d, ACS 2019-2023)', sum(ageCount)), ...
      'FontSize', 13);
print('fig_age_distribution.png', '-dpng', '-r200');

fprintf('\nUnder 15 plus 65 and over: %.1f%% of residents\n', ...
        agePct(1) + agePct(end));

%% ---------- Slide 4b: Risk factors, Cuba vs Sandoval County ----------
fid = fopen('cuba_risk_factors.csv', 'r');
header = fgetl(fid);
R = textscan(fid, '%s %f %f', 'Delimiter', ',');
fclose(fid);

measure  = R{1};
cuba     = R{2};
sandoval = R{3};

% plot largest at the top
n = numel(measure);
order = n:-1:1;

figure('Position', [100 100 900 550]);
h = barh([cuba(order) sandoval(order)], 0.8);
set(h(1), 'FaceColor', [0.85 0.37 0.01]);   % Cuba
set(h(2), 'FaceColor', [0.60 0.60 0.60]);   % Sandoval County
set(gca, 'YTick', 1:n, 'YTickLabel', measure(order), 'FontSize', 11);
xlabel('Percent of adults');
xlim([0 50]);
legend({'Cuba', 'Sandoval County'}, 'Location', 'southeast');
title('Health risk factors among adults, 2023 (CDC PLACES)', 'FontSize', 13);
print('fig_risk_factors.png', '-dpng', '-r200');

fprintf('\nCuba minus Sandoval County (percentage points):\n');
for k = 1:n
    fprintf('  %-28s %+5.1f\n', measure{k}, cuba(k) - sandoval(k));
end
